"""Web dashboard — Phase A1: FastAPI backend + single-user auth.

Wraps the same engine the CLI uses (Store / pipeline / images / publisher)
behind a session login. This phase ships auth, a minimal dashboard, the JSON
API for the core actions, and safe asset downloads. Long-running build/render
move to a background worker in Phase A2; richer screens come in A4.

Run it:  `chrgd serve`  (installs: `pip install -e '.[web]'`).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .character import charges_for_route
from .config import Settings, get_settings
from .db import Store
from .models import MAX_SLIDES, MIN_SLIDES, Idea, PostType, Status
from .webauth import auth_configured, verify_credentials
from .worker import (
    Worker,
    enqueue_angles,
    enqueue_build,
    enqueue_build_one,
    enqueue_render,
    enqueue_render_slide,
    enqueue_revise,
    enqueue_scroll_test,
    enqueue_video,
)

# Days a topical idea stays fresh before the calendar flags it as going stale.
_DECAY_SHELF_DAYS = {"days": 5, "weeks": 21}

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _store(settings: Settings) -> Store:
    settings.ensure_dirs()
    return Store(settings.db_path)


def _safe_output_path(settings: Settings, *parts: str) -> Path:
    """Resolve a path under output_dir, refusing traversal outside it."""
    base = Path(settings.output_dir).resolve()
    target = base.joinpath(*parts).resolve()
    if base != target and base not in target.parents:
        raise HTTPException(status_code=404, detail="not found")
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    return target


_CHARACTER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


async def _apply_character_upload(
    settings: Settings,
    current: str,
    upload: UploadFile | None,
    remove: bool,
) -> str:
    """Reconcile the locked character portrait on a settings save.

    Returns the filename (under output_dir/_brand/) to store on the profile:
    the new upload's name, '' if removed, or `current` unchanged. Keeps exactly
    one portrait on disk (stored as `character.<ext>`).
    """
    from .profile import brand_asset_dir

    asset_dir = brand_asset_dir(settings)

    def _clear_existing() -> None:
        if asset_dir.exists():
            for old in asset_dir.glob("character.*"):
                old.unlink(missing_ok=True)

    if remove:
        _clear_existing()
        return ""

    filename = (upload.filename or "").strip() if upload else ""
    if not filename:
        return current  # no file chosen — leave the saved portrait alone

    ext = Path(filename).suffix.lower()
    if ext not in _CHARACTER_EXTS:
        raise HTTPException(
            status_code=400,
            detail="character portrait must be a PNG, JPG or WEBP image",
        )
    data = await upload.read()
    if not data:
        return current
    _clear_existing()
    asset_dir.mkdir(parents=True, exist_ok=True)
    dest = asset_dir / f"character{ext}"
    dest.write_bytes(data)
    return dest.name


def create_app(settings: Settings | None = None, *, run_worker: bool = True) -> FastAPI:
    settings = settings or get_settings()
    from .logging_setup import configure_logging

    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        workers: list[Worker] = []
        if run_worker:
            from .worker import RESEARCH_KINDS, VIDEO_KINDS

            # Recover interrupted jobs ONCE, before any worker starts, so a
            # starting worker can't reap the other's in-flight job. Video jobs
            # are requeued (they resume from persisted state) rather than reaped.
            with _store(settings) as store:
                store.recover_interrupted_jobs()
                store.requeue_interrupted_video_jobs()
            # Three lanes: research (slow web-search scans) and video (minutes of
            # provider polling per clip) each get their own worker so neither can
            # delay the fast lane (builds, renders, takes).
            # The fast lane ticks quickly: a 1s idle wait added up to a second of
            # dead time before every interactive job (concepts, takes, builds)
            # even started. Claiming a job is one guarded UPDATE on a local
            # SQLite file, so 5 ticks/sec costs nothing and the UI feels instant.
            fast = Worker(
                settings, poll_interval=0.2, exclude=RESEARCH_KINDS | VIDEO_KINDS,
                name="chrgd-fast", recover_on_start=False,
            )
            research = Worker(
                settings, kinds=RESEARCH_KINDS,
                name="chrgd-research", recover_on_start=False,
            )
            video = Worker(
                settings, kinds=VIDEO_KINDS,
                name="chrgd-video", recover_on_start=False,
            )
            fast.start()
            research.start()
            video.start()
            workers = [fast, research, video]
            app.state.workers = workers
        try:
            yield
        finally:
            for w in workers:
                w.stop()

    app = FastAPI(title="CHRGD Content Studio", lifespan=lifespan)
    app.state.settings = settings
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    secret_seed = settings.secret_key or secrets.token_hex(32)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_seed,
        session_cookie="chrgd_session",
        same_site="lax",
        https_only=False,  # Caddy terminates TLS; set true if serving TLS direct
    )
    # Unguessable path segment for the login-free /media/ route (Metricool's
    # bulk import fetches images itself, so those URLs can't sit behind the
    # session). Derived from the secret key so links survive restarts —
    # production must set CHRGD_SECRET_KEY or exported URLs go stale.
    media_token = hashlib.sha256(f"chrgd-media:{secret_seed}".encode()).hexdigest()[:24]
    app.state.media_token = media_token

    # --- auth helpers -------------------------------------------------------

    def current_user(request: Request) -> Optional[str]:
        return request.session.get("user")

    def require_user(request: Request) -> str:
        user = current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="login required")
        return user

    def require_user_page(request: Request):
        """Like require_user but redirects browsers to /login."""
        if not current_user(request):
            raise HTTPException(
                status_code=307, headers={"Location": "/login"}, detail="redirect"
            )
        return current_user(request)

    # --- auth routes --------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if current_user(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"configured": auth_configured(settings), "user": None},
        )

    @app.post("/login")
    def login(request: Request, username: str = Form(""), password: str = Form("")):
        if verify_credentials(settings, username, password):
            request.session["user"] = username
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Invalid username or password.",
                "configured": auth_configured(settings),
                "user": None,
            },
            status_code=401,
        )

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # --- page rendering helper ---------------------------------------------

    def render_page(request: Request, name: str, active: str, **ctx):
        with _store(settings) as store:
            review_count = store.count(Status.review)
            queue_active = store.conn.execute(
                "SELECT COUNT(*) AS c FROM jobs "
                "WHERE status IN ('QUEUED','PROCESSING')"
            ).fetchone()["c"]
        ctx.update(
            request=request,
            user=current_user(request),
            active=active,
            review_count=review_count,
            queue_active=int(queue_active),
            flash=request.query_params.get("flash"),
        )
        return templates.TemplateResponse(request=request, name=name, context=ctx)

    def _counts(store) -> dict:
        exported = store.conn.execute(
            "SELECT COUNT(*) AS c FROM ideas WHERE exported_at IS NOT NULL"
        ).fetchone()["c"]
        return {
            "queued": store.count(Status.queued),
            "done": store.count(Status.done),
            "review": store.count(Status.review),
            "exported": int(exported),
            "total": store.count(),
        }

    def _post_view(idea) -> dict:
        slides = json.loads(idea.slides_json) if idea.slides_json else []
        hashtags = json.loads(idea.hashtags) if idea.hashtags else []
        route = json.loads(idea.route_json) if idea.route_json else {}
        assets = (
            [Path(p).name for p in json.loads(idea.asset_paths_json)]
            if idea.asset_paths_json
            else []
        )
        return {
            "idea_id": idea.idea_id,
            "status": idea.status.value,
            "concept_note": idea.concept_note,
            "hook": idea.hook,
            "caption": idea.caption,
            "comment_trigger": idea.comment_trigger,
            "hashtags": " ".join(hashtags),
            "slides": slides,
            "qa": route.get("qa", {}),
            "assets": assets,
        }

    # --- public media (Metricool URL-mode imports) ----------------------------

    @app.get("/media-pub/{token}/{filename}")
    def public_media(token: str, filename: str):
        """Serve an exported ready/ image without a login.

        Metricool's bulk importer fetches media itself, so these URLs must
        work unauthenticated. Security: the unguessable token is compared in
        constant time, only files inside output/ready are reachable (no
        traversal), and only image types are served."""
        if not secrets.compare_digest(token, media_token):
            raise HTTPException(status_code=404, detail="not found")
        target = _safe_output_path(settings, "ready", filename)
        if target.suffix.lower() not in (".jpg", ".jpeg", ".webp", ".png"):
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    def _metricool_publisher(request: Request):
        """The CSV publisher, with URL-mode media auto-pointed at this
        studio's public /media/ route when no external host is configured."""
        from .publisher import get_publisher

        publisher = get_publisher("metricool_csv")
        fmt = publisher.cols.format
        if fmt.media_reference == "url" and not fmt.media_base_url:
            base = str(request.base_url).rstrip("/")
            fmt.media_base_url = f"{base}/media-pub/{media_token}"
        return publisher

    # --- pages (HTML) -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, _: str = Depends(require_user_page)):
        # The dashboard folded into the calendar header — two journeys, not a hub.
        flash = request.query_params.get("flash")
        target = f"/calendar?flash={flash}" if flash else "/calendar"
        return RedirectResponse(target, status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, _: str = Depends(require_user_page)):
        with _store(settings) as store:
            counts = _counts(store)
            spend = float(
                store.conn.execute(
                    "SELECT COALESCE(SUM(spend_usd),0) AS s FROM runs"
                ).fetchone()["s"]
            )
            scheduled = [
                Idea.model_validate(dict(r))
                for r in store.conn.execute(
                    "SELECT * FROM ideas WHERE scheduled_for IS NOT NULL "
                    "ORDER BY scheduled_for ASC LIMIT 20"
                ).fetchall()
            ]
            runs = [dict(r) for r in store.list_runs(20)]
        return render_page(
            request, "home.html", "dashboard",
            counts=counts, spend=spend, scheduled=scheduled, runs=runs,
        )

    def _amp_situations(settings) -> list[dict]:
        from .character import load_situations, used_situations

        with _store(settings) as store:
            used = used_situations(store)
        return [
            {"key": s.key, "label": s.label, "setup": s.setup,
             "state": s.default_state(), "tips": s.tips,
             "used": s.key in used}
            for s in load_situations().values()
        ]

    def _ingredient_list(settings) -> list[dict]:
        from .ingredients import covered, load_ingredients

        with _store(settings) as store:
            done = covered(store)
        return [
            {"key": i.key, "label": i.label, "question": i.question,
             "covered": i.key in done}
            for i in load_ingredients().values()
        ]

    def _session_nudge(settings) -> str:
        from .sessions import suggestion

        with _store(settings) as store:
            return suggestion(store)

    def _roster_with_canon(settings) -> list[dict]:
        """The roster, plus what the show has actually established for each —
        so the cast picker shows who's carrying a storyline, not just a trait."""
        from .roster import load_roster
        from .series import load_canon

        with _store(settings) as store:
            canon = load_canon(store)
        out = []
        for c in load_roster().values():
            rec = canon.for_character(c.key)
            out.append({
                "key": c.key, "name": c.name, "cls": c.cls, "what": c.what,
                "trait": c.trait, "protected": c.protected,
                "catchphrase": c.catchphrase,
                "standing": canon.standing_for(c.key),
                "gags": rec.gags,
                "episodes": rec.episodes,
            })
        return out

    def _canon_summary(settings) -> dict:
        from .series import load_canon

        with _store(settings) as store:
            canon = load_canon(store)
        return {
            "season": canon.season.label,
            "episode": canon.next_number(),
            "open_thread": canon.open_thread(),
            "history": [e.line() for e in canon.recent()],
        }

    @app.get("/create", response_class=HTMLResponse)
    def create_page(
        request: Request, idea: str | None = None, day: str | None = None,
        _: str = Depends(require_user_page),
    ):
        from .character import AMP_STATES, STATE_ORDER
        from .mechanics import load_mechanics
        from .roster import load_roster
        from .sessions import load_axes
        from .shows import ordered_shows
        from .territories import in_season

        # No style preset picker: the engine designs a bespoke design_system
        # per post, so the journey goes source → door directly.
        # The five recurring shows are server-rendered so the picker paints on
        # first load rather than after a round-trip.
        return render_page(
            request, "create.html", "create",
            mechanics=[m.model_dump() for m in load_mechanics().values()],
            shows=[
                {
                    "key": sh.key, "label": sh.label, "tagline": sh.tagline,
                    "blurb": sh.blurb, "icon": sh.icon, "weekday": sh.weekday,
                }
                for sh in ordered_shows()
            ],
            # AMP's own screen: the situation bank with what's been used
            # recently marked (a nudge, never a rule — D12) and his states.
            amp_situations=_amp_situations(settings),
            amp_states=[
                {"key": k, "label": AMP_STATES[k]["label"],
                 "mood": AMP_STATES[k]["mood"]}
                for k in STATE_ORDER
            ],
            # STRAIGHT UP's own screen: the ingredient library, with what's
            # already been covered marked.
            ingredients=_ingredient_list(settings),
            # THE SESSION's own screen: the variant matrix + the staleness nudge.
            session_axes=[
                {"key": a.key, "label": a.label, "required": a.required,
                 "options": [{"key": k, "label": v} for k, v in a.options.items()]}
                for a in load_axes().values()
            ],
            session_nudge=_session_nudge(settings),
            # THE MULTIVERSE's own screen: the roster and where the canon is.
            roster=_roster_with_canon(settings),
            canon=_canon_summary(settings),
            # LIVE WIRE's own screen: the in-season interest territories.
            territories=[
                {"key": t.key, "label": t.label, "weight": t.weight}
                for t in in_season()
            ],
            resume_idea=idea or "",
            preset_day=day or "",
        )

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(request: Request, _: str = Depends(require_user_page)):
        return render_page(request, "queue.html", "queue")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, _: str = Depends(require_user_page)):
        from .profile import load_profile

        with _store(settings) as store:
            profile = load_profile(store)
        return render_page(
            request, "settings.html", "settings", profile=profile.model_dump()
        )

    @app.post("/settings")
    async def settings_save(
        request: Request,
        brand_name: str = Form(""),
        one_liner: str = Form(""),
        voice: str = Form(""),
        audience: str = Form(""),
        dos: str = Form(""),
        donts: str = Form(""),
        pillars: str = Form(""),
        handle: str = Form(""),
        default_hashtags: str = Form(""),
        house_style: str = Form(""),
        palette: str = Form(""),
        type_style: str = Form(""),
        character: str = Form(""),
        motif: str = Form(""),
        swipe_style: str = Form("cohesive"),
        supporting_cast: str = Form(""),
        casting_intensity: str = Form("off"),
        character_image: UploadFile | None = File(None),
        remove_character_image: str = Form(""),
        _: str = Depends(require_user_page),
    ):
        from .profile import (
            BrandProfile,
            brand_asset_dir,
            load_profile,
            save_profile,
            validate_supporting_cast,
        )

        # Hard guardrail: refuse an unsafe casting direction at save time.
        blocked = validate_supporting_cast(supporting_cast)
        if blocked:
            return RedirectResponse(
                "/settings?flash=Casting+direction+rejected%3A+remove+%22"
                f"{blocked}%22+%E2%80%94+adults+in+gymwear+only", 303
            )
        casting_intensity = (
            casting_intensity if casting_intensity in ("off", "natural", "elevated")
            else "off"
        )

        with _store(settings) as store:
            # Start from the saved portrait so a plain save doesn't drop it.
            portrait = load_profile(store).character_image.strip()
            portrait = await _apply_character_upload(
                settings, portrait, character_image, bool(remove_character_image)
            )
            profile = BrandProfile(
                brand_name=brand_name, one_liner=one_liner, voice=voice,
                audience=audience, dos=dos, donts=donts, pillars=pillars,
                handle=handle,
                default_hashtags=default_hashtags, house_style=house_style,
                palette=palette, type_style=type_style, character=character,
                character_image=portrait, motif=motif,
                swipe_style="pan" if swipe_style == "pan" else "cohesive",
                supporting_cast=supporting_cast, casting_intensity=casting_intensity,
            )
            save_profile(store, profile)
        return RedirectResponse(
            "/settings?flash=Saved+%E2%80%94+applies+to+your+next+build", 303
        )

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar_page(request: Request, _: str = Depends(require_user_page)):
        from .publisher import load_columns

        sched = load_columns().schedule
        with _store(settings) as store:
            counts = _counts(store)
            spend = float(
                store.conn.execute(
                    "SELECT COALESCE(SUM(spend_usd),0) AS s FROM runs"
                ).fetchone()["s"]
            )
        return render_page(
            request, "calendar.html", "calendar",
            counts=counts, spend=spend,
            default_time=(sched.times[0] if sched.times else "18:00"),
            per_day=sched.per_day,
        )

    @app.get("/backlog", response_class=HTMLResponse)
    def backlog_page(
        request: Request, status: str | None = None, _: str = Depends(require_user_page)
    ):
        st = Status(status) if status else None
        with _store(settings) as store:
            ideas = store.list_ideas(status=st)
        return render_page(
            request, "backlog.html", "backlog",
            ideas=ideas, statuses=[s.value for s in Status], current_status=status or "",
        )

    @app.get("/build", response_class=HTMLResponse)
    def build_page(request: Request, _: str = Depends(require_user_page)):
        with _store(settings) as store:
            counts = _counts(store)
        return render_page(request, "build.html", "build", counts=counts)

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request, _: str = Depends(require_user_page)):
        with _store(settings) as store:
            review = store.list_ideas(status=Status.review)
            done = [i for i in store.list_ideas(status=Status.done) if i.slides_json]
        posts = [_post_view(i) for i in review] + [_post_view(i) for i in done]
        return render_page(request, "review.html", "review", posts=posts)

    @app.get("/trends", response_class=HTMLResponse)
    def trends_page(request: Request, _: str = Depends(require_user_page)):
        return render_page(request, "trends.html", "trends")

    @app.get("/export", response_class=HTMLResponse)
    def export_page(request: Request, sample: int = 0, _: str = Depends(require_user_page)):
        publisher = _metricool_publisher(request)
        if sample:
            publisher.write_sample(settings)
            return RedirectResponse("/export?flash=Sample+CSV+written+to+ready/", 303)

        ready = Path(settings.output_dir) / "ready"
        csvs = []
        if ready.exists():
            for f in sorted(ready.glob("*.csv"), reverse=True):
                csvs.append({"name": f.name, "size": f"{f.stat().st_size} B"})
        with _store(settings) as store:
            exported = [
                Idea.model_validate(dict(r))
                for r in store.conn.execute(
                    "SELECT * FROM ideas WHERE exported_at IS NOT NULL "
                    "ORDER BY scheduled_for ASC"
                ).fetchall()
            ]
            ready_count = len(publisher._selectable(store, None))
        return render_page(
            request, "export.html", "export",
            csvs=csvs, exported=exported, ready_count=ready_count,
        )

    @app.post("/capture")
    def capture_form(
        request: Request,
        _: str = Depends(require_user_page),
        dump: str = Form(""),
        category: str = Form(""),
        priority: int = Form(3),
    ):
        from .capture import capture_ideas

        if dump.strip():
            with _store(settings) as store:
                created, skipped = capture_ideas(
                    store, settings, dump, priority=priority, content_category=category
                )
            flash = f"Captured {len(created)}, skipped {len(skipped)} duplicate(s)."
        else:
            flash = "Nothing to capture."
        return RedirectResponse(f"/backlog?flash={flash}", status_code=303)

    @app.post("/export")
    def export_run(request: Request, _: str = Depends(require_user_page)):
        publisher = _metricool_publisher(request)
        limit = publisher.cols.schedule.per_day * 7
        with _store(settings) as store:
            result = publisher.export(store, settings, limit=limit)
        n = len(result.exported_ids)
        flash = f"Exported {n} post(s)." if n else "Nothing to export (need rendered posts)."
        return RedirectResponse(f"/export?flash={flash}", status_code=303)

    # --- JSON API -----------------------------------------------------------

    @app.get("/api/backlog")
    def api_backlog(request: Request, status: str | None = None, _: str = Depends(require_user)):
        st = Status(status) if status else None
        with _store(settings) as store:
            return [i.model_dump(mode="json") for i in store.list_ideas(status=st)]

    @app.post("/api/capture")
    def api_capture(
        request: Request,
        _: str = Depends(require_user),
        dump: str = Form(...),
        priority: int = Form(3),
        category: str = Form(""),
    ):
        from .capture import capture_ideas

        with _store(settings) as store:
            created, skipped = capture_ideas(
                store, settings, dump, priority=priority, content_category=category
            )
        return {
            "created": [i.idea_id for i in created],
            "skipped": skipped,
        }

    @app.get("/api/ideas/{idea_id}")
    def api_idea(idea_id: str, _: str = Depends(require_user)):
        with _store(settings) as store:
            idea = store.get_idea(idea_id)
        if idea is None:
            raise HTTPException(404, "no such idea")
        return idea.model_dump(mode="json")

    @app.post("/api/ideas/{idea_id}/void")
    def api_void(idea_id: str, _: str = Depends(require_user)):
        with _store(settings) as store:
            if store.get_idea(idea_id) is None:
                raise HTTPException(404, "no such idea")
            store.set_status(idea_id, Status.void)
        return {"idea_id": idea_id, "status": "void"}

    @app.post("/api/ideas/{idea_id}/edit")
    async def api_edit(idea_id: str, request: Request, _: str = Depends(require_user)):
        """Edit approved copy (hook, caption, slides, hashtags) from the review UI."""
        form = await request.form()
        with _store(settings) as store:
            idea = store.get_idea(idea_id)
            if idea is None:
                raise HTTPException(404, "no such idea")

            slides = json.loads(idea.slides_json) if idea.slides_json else []

            # The editor can add/remove slides: `slide_count` resizes the list
            # (within the platform bounds) before per-slide fields apply. New
            # slides start empty; requests without it leave the count alone.
            if "slide_count" in form:
                try:
                    n = int(str(form["slide_count"]))
                except ValueError:
                    raise HTTPException(400, "slide_count must be a number")
                if not MIN_SLIDES <= n <= MAX_SLIDES:
                    raise HTTPException(
                        400, f"slide_count must be {MIN_SLIDES}-{MAX_SLIDES}"
                    )
                while len(slides) < n:
                    slides.append(
                        {"headline": "", "supporting": "", "body": "",
                         "image_prompt": "", "visual_intent": ""}
                    )
                slides = slides[:n]

            for i, slide in enumerate(slides):
                if f"slide_headline_{i}" in form:
                    slide["headline"] = form[f"slide_headline_{i}"]
                if f"slide_supporting_{i}" in form:
                    slide["supporting"] = form[f"slide_supporting_{i}"]
                if f"slide_body_{i}" in form:
                    slide["body"] = form[f"slide_body_{i}"]
                if f"slide_image_prompt_{i}" in form:
                    slide["image_prompt"] = form[f"slide_image_prompt_{i}"]

            hashtags = [
                t if t.startswith("#") else f"#{t}"
                for t in str(form.get("hashtags", "")).split()
                if t
            ]
            fields = {
                "hook": form.get("hook", idea.hook),
                "caption": form.get("caption", idea.caption),
                "comment_trigger": form.get("comment_trigger", idea.comment_trigger),
                "hashtags": json.dumps(hashtags),
                "slides_json": json.dumps(slides),
            }
            # save_build stamps status=done; keep review posts in review.
            prev = idea.status
            store.save_build(idea_id, fields)
            if prev == Status.review:
                store.set_status(idea_id, Status.review)

        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/review?flash=Saved", status_code=303)
        return {"idea_id": idea_id, "saved": True}

    @app.post("/api/ideas/{idea_id}/approve")
    def api_approve(idea_id: str, request: Request, _: str = Depends(require_user)):
        """Override a QA-flagged post to done so it can be exported."""
        with _store(settings) as store:
            idea = store.get_idea(idea_id)
            if idea is None:
                raise HTTPException(404, "no such idea")
            store.set_status(idea_id, Status.done)
        # Browser form posts land back on the review page.
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/review", status_code=303)
        return {"idea_id": idea_id, "status": "done"}

    @app.post("/api/render/{idea_id}")
    def api_render(idea_id: str, dry_run: bool = False, _: str = Depends(require_user)):
        """Render synchronously. Goes through `render_idea` like every other
        render path, so the slide-1 concept gate can't be sidestepped by using
        this endpoint instead of the job queue."""
        from .images import ImageError
        from .services import render_idea

        with _store(settings) as store:
            idea = store.get_idea(idea_id)
            if idea is None:
                raise HTTPException(404, "no such idea")
            try:
                result = render_idea(store, settings, idea, dry_run=dry_run)
            except ImageError as exc:
                raise HTTPException(400, str(exc))
        return {"idea_id": idea_id, "paths": result.paths, "spend_usd": result.spend_usd}

    # --- background jobs ----------------------------------------------------

    @app.post("/api/jobs/build")
    def api_job_build(
        request: Request,
        count: int = Form(3),
        dry_run: bool = Form(False),
        _: str = Depends(require_user),
    ):
        with _store(settings) as store:
            job_id = enqueue_build(store, count=count, dry_run=dry_run)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(f"/?flash=Build+job+%23{job_id}+queued", 303)
        return {"job_id": job_id, "kind": "build"}

    @app.post("/api/jobs/render/{idea_id}")
    def api_job_render(
        idea_id: str, dry_run: bool = False, _: str = Depends(require_user)
    ):
        with _store(settings) as store:
            if store.get_idea(idea_id) is None:
                raise HTTPException(404, "no such idea")
            job_id = enqueue_render(store, idea_id, dry_run=dry_run)
        return {"job_id": job_id, "kind": "render", "idea_id": idea_id}

    @app.post("/api/jobs/video/{idea_id}")
    def api_job_video(idea_id: str, _: str = Depends(require_user)):
        """Queue a reel render for a rendered carousel (image-to-video).

        Guarded by the feature flag: OFF → 409 with the enable hint, so the UI
        can surface it without the request 500-ing.
        """
        from .video import VideoDisabledError, ensure_video_enabled

        try:
            ensure_video_enabled(settings)
        except VideoDisabledError as exc:
            raise HTTPException(409, str(exc))
        with _store(settings) as store:
            idea = store.get_idea(idea_id)
            if idea is None:
                raise HTTPException(404, "no such idea")
            if not idea.asset_paths_json:
                raise HTTPException(400, "render the carousel before making a reel")
            job_id = enqueue_video(store, idea_id)
        return {"job_id": job_id, "kind": "video", "idea_id": idea_id}

    @app.post("/api/scroll-test/{idea_id}")
    def api_scroll_test(idea_id: str, _: str = Depends(require_user)):
        """Cold scroll test on the finished slide 1 → a job the UI polls."""
        from .scrolltest import _slide_one_path

        with _store(settings) as store:
            idea = store.get_idea(idea_id)
            if idea is None:
                raise HTTPException(404, "no such idea")
            if _slide_one_path(idea) is None:
                raise HTTPException(400, "render the carousel before scroll-testing")
            job_id = enqueue_scroll_test(store, idea_id)
        return {"job_id": job_id, "kind": "scroll_test", "idea_id": idea_id}

    @app.post("/api/jobs/run")
    def api_job_run(
        request: Request,
        count: int = Form(5),
        scout: bool = Form(False),
        dry_run: bool = Form(False),
        _: str = Depends(require_user),
    ):
        from .worker import enqueue_run

        with _store(settings) as store:
            job_id = enqueue_run(store, count=count, scout=scout, dry_run=dry_run)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(f"/build?flash=Run+job+%23{job_id}+queued", 303)
        return {"job_id": job_id, "kind": "run"}

    @app.post("/api/jobs/trends")
    def api_job_trends(
        request: Request, count: int = Form(6), _: str = Depends(require_user)
    ):
        from .worker import enqueue_trends

        with _store(settings) as store:
            job_id = enqueue_trends(store, count=count)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/trends", 303)
        return {"job_id": job_id, "kind": "trends"}

    @app.post("/api/trends/seed/{job_id}")
    def api_trends_seed(job_id: int, request: Request, _: str = Depends(require_user)):
        """Seed the trends captured by a completed scout job as backlog rows."""
        from .trends import Trend, seed_trends

        with _store(settings) as store:
            job = store.get_job(job_id)
            if job is None or job["kind"] != "trends":
                raise HTTPException(404, "no such trends job")
            data = json.loads(job["result_json"] or "{}")
            trends = [Trend.model_validate(t) for t in data.get("trends", [])]
            outcome = seed_trends(store, settings, trends)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(
                f"/backlog?flash=Seeded+{len(outcome.created)}+trend+row(s)", 303
            )
        return {
            "created": [i.idea_id for i in outcome.created],
            "skipped": outcome.skipped,
        }

    # --- create journey -----------------------------------------------------

    def _parse_when(raw: str | None) -> datetime | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            raise HTTPException(400, f"bad datetime: {raw!r}")

    def _job_result(job: dict) -> dict:
        """Whatever a running job last wrote into result_json ({} if nothing)."""
        if not job.get("result_json"):
            return {}
        try:
            payload = json.loads(job["result_json"])
        except (json.JSONDecodeError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _job_note(job: dict) -> str | None:
        """The live status note a running job last wrote into result_json."""
        return _job_result(job).get("note")

    def _idea_or_404(store: Store, idea_id: str) -> Idea:
        idea = store.get_idea(idea_id)
        if idea is None:
            raise HTTPException(404, "no such idea")
        return idea

    def _new_seed(
        store: Store,
        *,
        concept_note: str,
        content_category: str = "",
        target_viewer: str = "",
        pain_point: str = "",
        core_tension: str = "",
        style: str = "",
        mechanic_key: str = "",
        length: str = "",
        scheduled_for: datetime | None = None,
        moment: dict | None = None,
        show: str = "",
        amp: dict | None = None,
        ingredient: str = "",
        session_variant: dict | None = None,
        cast: list[str] | None = None,
    ) -> Idea:
        """One seed row carrying the create journey's up-front choices."""
        from .mechanics import get_mechanic
        from .shows import ROUTE_KEY, get_show

        route: dict = {}
        # Which recurring show this post is an episode of. It rides route_json
        # exactly like style and mechanic_lock, so creation_prefs carries it
        # through every rebuild; absent for an off-format post.
        if show:
            if get_show(show) is None:
                raise HTTPException(400, f"unknown show '{show}'")
            route[ROUTE_KEY] = show
        # AMP's journey: the situation, his state and the tip the post owes.
        # These drive the build brief (character.situation_brief) and, for the
        # state, the locked character prefix on every image.
        if amp:
            from .character import AMP_STATES, get_situation

            if amp.get("situation") and get_situation(amp["situation"]) is None:
                raise HTTPException(400, f"unknown situation '{amp['situation']}'")
            if amp.get("state") and amp["state"] not in AMP_STATES:
                raise HTTPException(400, f"unknown Amp state '{amp['state']}'")
            for src, key in (("situation", "amp_situation"),
                             ("situation_text", "amp_situation_text"),
                             ("state", "amp_state"), ("tip", "amp_tip")):
                if amp.get(src):
                    route[key] = amp[src]
        # STRAIGHT UP's subject — the library entry the post is written from.
        if ingredient:
            from .ingredients import ROUTE_KEY as ING_KEY, get_ingredient

            if get_ingredient(ingredient) is None:
                raise HTTPException(400, f"unknown ingredient '{ingredient}'")
            route[ING_KEY] = ingredient
        # THE SESSION's point in the variant matrix. Required axes are checked
        # here rather than in the matrix module, which stays permissive so a
        # renamed option can't 500 an old bookmark.
        if session_variant:
            from .sessions import ROUTE_KEY as SESSION_KEY, missing_required, validate

            missing = missing_required(session_variant)
            if missing:
                raise HTTPException(400, "still to pick: " + ", ".join(missing))
            route[SESSION_KEY] = validate(session_variant)
        # THE MULTIVERSE's cast. Validated against the approved roster here,
        # because "the engine never free-picks a person" is a likeness rule and
        # this is the edge it has to hold at.
        if cast:
            from .roster import ROUTE_KEY as CAST_KEY, get_character, load_world

            unknown = [k for k in cast if get_character(k) is None]
            if unknown:
                raise HTTPException(
                    400, f"not on the roster: {', '.join(unknown)}"
                )
            limit = load_world().max_cast
            if len(cast) > limit:
                raise HTTPException(
                    400, f"pick at most {limit} characters — a carousel can't hold more"
                )
            route[CAST_KEY] = list(cast)
        if moment:
            route["moment"] = moment
        if style:
            route["style"] = style
        if length:
            if length not in ("quick", "standard", "deep"):
                raise HTTPException(400, f"unknown length preference '{length}'")
            route["length_pref"] = length
        if mechanic_key:
            mech = get_mechanic(mechanic_key)
            if mech is None:
                raise HTTPException(400, f"unknown mechanic '{mechanic_key}'")
            # `key` rides along with the human label so downstream code can
            # identify the mechanic itself (the Amp journey keys off it); the
            # label stays as `name` because the build prompt and detail view
            # both show it to a human.
            route["mechanic_lock"] = {
                "key": mech.key,
                "name": mech.label,
                "skeleton": mech.skeleton,
            }
        idea = Idea(
            idea_id=store.next_idea_id(settings.id_prefix),
            concept_note=concept_note,
            content_category=content_category,
            target_viewer=target_viewer,
            pain_point=pain_point,
            core_tension=core_tension,
            route_json=json.dumps(route) if route else None,
            scheduled_for=scheduled_for,
        )
        return store.add_idea(idea)

    @app.post("/api/create/start")
    def api_create_start(
        request: Request,
        mode: str = Form(...),  # 'idea' | 'facts' | 'blank'
        text: str = Form(""),
        show: str = Form(""),
        amp_situation: str = Form(""),
        amp_situation_text: str = Form(""),
        amp_state: str = Form(""),
        amp_tip: str = Form(""),
        ingredient: str = Form(""),
        variant: str = Form(""),   # JSON object: {axis: option}
        cast: str = Form(""),      # comma-separated roster keys
        mechanic: str = Form(""),
        style: str = Form(""),
        length: str = Form(""),
        scheduled_for: str = Form(""),
        manual: bool = Form(False),
        develop: bool = Form(False),
        takes: bool = Form(False),
        ragebait: bool = Form(False),
        _: str = Depends(require_user),
    ):
        """Open one of the three doors into the create journey."""
        from .worker import enqueue_concept, enqueue_takes

        when = _parse_when(scheduled_for)
        amp = {
            "situation": amp_situation, "situation_text": amp_situation_text,
            "state": amp_state, "tip": amp_tip,
        }
        amp = amp if any(amp.values()) else None
        try:
            variant_obj = json.loads(variant) if variant.strip() else None
        except json.JSONDecodeError:
            raise HTTPException(400, "variant must be a JSON object") from None
        if variant_obj is not None and not isinstance(variant_obj, dict):
            raise HTTPException(400, "variant must be a JSON object")

        # The default journey fans out to competing takes first; develop and
        # straight-build remain as explicit choices.
        def _first_job(store: Store, idea_id: str) -> int:
            if takes:
                return enqueue_takes(store, idea_id)
            return (
                enqueue_concept(store, idea_id)
                if develop
                else enqueue_build_one(store, idea_id)
            )

        with _store(settings) as store:
            if mode == "facts":
                if not text.strip():
                    raise HTTPException(400, "paste some facts first")
                job_id = enqueue_angles(store, facts=text)
                return {"mode": mode, "job_id": job_id}

            if mode == "idea":
                if not text.strip():
                    raise HTTPException(400, "give the idea a line of text")
                # A hand-written hot take enters the ragebait flow directly:
                # the seed carries the ragebait moment so the build commits to
                # the fight framing, same as a scouted debate.
                idea = _new_seed(
                    store, concept_note=text.strip(), style=style,
                    content_category="ragebait" if ragebait else "",
                    # An Amp concept from the create screen carries the
                    # charge-cycle mechanic through this door, so it builds and
                    # renders as an Amp post instead of a carousel that merely
                    # mentions him.
                    mechanic_key=mechanic,
                    length=length, scheduled_for=when, show=show, amp=amp,
                    ingredient=ingredient, session_variant=variant_obj,
                    cast=[c for c in cast.split(",") if c.strip()],
                    moment=(
                        {"kind": "ragebait", "title": text.strip(),
                         "angle": text.strip()}
                        if ragebait else None
                    ),
                )
                job_id = _first_job(store, idea.idea_id)
                return {
                    "mode": mode, "idea_id": idea.idea_id,
                    "job_id": job_id, "develop": develop, "takes": takes,
                }

            if mode == "blank":
                idea = _new_seed(
                    store,
                    concept_note=text.strip() or "blank canvas",
                    style=style,
                    mechanic_key=mechanic,
                    length=length,
                    scheduled_for=when,
                    show=show,
                )
                if manual:
                    # Fully manual: empty slides straight into the editor
                    # (count follows the length nudge; the editor can add or
                    # remove slides freely), flagged review so nothing ships
                    # without an explicit approve.
                    n = {"quick": 2, "deep": 8}.get(length, 5)
                    empty = [
                        {"headline": "", "supporting": "", "body": "",
                         "image_prompt": "", "visual_intent": ""}
                        for _i in range(n)
                    ]
                    store.mark_review(
                        idea.idea_id,
                        {
                            "post_type": PostType.carousel.value,
                            "hook": "",
                            "slides_json": json.dumps(empty),
                        },
                    )
                    return {"mode": mode, "idea_id": idea.idea_id, "manual": True}
                job_id = _first_job(store, idea.idea_id)
                return {
                    "mode": mode, "idea_id": idea.idea_id,
                    "job_id": job_id, "develop": develop, "takes": takes,
                }

        raise HTTPException(400, f"unknown mode '{mode}'")

    @app.post("/api/create/angle/{job_id}")
    def api_create_angle(
        job_id: int,
        index: int = Form(...),
        style: str = Form(""),
        length: str = Form(""),
        scheduled_for: str = Form(""),
        develop: bool = Form(False),
        _: str = Depends(require_user),
    ):
        """Pick one of a completed angles job's takes and build/develop it."""
        when = _parse_when(scheduled_for)
        with _store(settings) as store:
            job = store.get_job(job_id)
            if job is None or job["kind"] != "angles":
                raise HTTPException(404, "no such angles job")
            angles = json.loads(job["result_json"] or "{}").get("angles", [])
            if not 0 <= index < len(angles):
                raise HTTPException(400, "angle index out of range")
            a = angles[index]
            idea = _new_seed(
                store,
                concept_note=a.get("concept_note", a.get("title", "")),
                content_category="fact",
                target_viewer=a.get("target_viewer", ""),
                pain_point=a.get("pain_point", ""),
                core_tension=a.get("core_tension", ""),
                style=style,
                length=length,
                scheduled_for=when,
            )
            if develop:
                from .worker import enqueue_concept

                next_job = enqueue_concept(store, idea.idea_id)
            else:
                next_job = enqueue_build_one(store, idea.idea_id)
        return {"idea_id": idea.idea_id, "job_id": next_job, "develop": develop}

    # --- the fan-out: competing takes before the expensive write ---------------

    @app.post("/api/ideas/{idea_id}/takes")
    def api_takes_start(
        idea_id: str, feedback: str = Form(""), _: str = Depends(require_user)
    ):
        """Start (or re-fan with feedback) a round of competing takes."""
        from .worker import enqueue_takes

        with _store(settings) as store:
            _idea_or_404(store, idea_id)
            job_id = enqueue_takes(store, idea_id, feedback=feedback.strip())
        return {"job_id": job_id, "kind": "takes", "idea_id": idea_id}

    @app.post("/api/ideas/{idea_id}/takes/{job_id}/pick")
    def api_takes_pick(
        idea_id: str,
        job_id: int,
        index: int = Form(...),
        tweak: str = Form(""),
        develop: bool = Form(False),
        _: str = Depends(require_user),
    ):
        """Lock one take as the agreed direction, then write (or develop) it."""
        from .worker import enqueue_concept

        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
            job = store.get_job(job_id)
            if job is None or job["kind"] != "takes" or job["idea_id"] != idea_id:
                raise HTTPException(404, "no such takes round for this idea")
            takes = json.loads(job["result_json"] or "{}").get("takes", [])
            if not 0 <= index < len(takes):
                raise HTTPException(400, "take index out of range")
            chosen = dict(takes[index])
            if tweak.strip():
                chosen["tweak"] = tweak.strip()
            # The chosen take rides route_json (like every creation pref) so it
            # survives the build overwriting the route.
            route = json.loads(idea.route_json) if idea.route_json else {}
            route["take"] = chosen
            store.conn.execute(
                "UPDATE ideas SET route_json = ? WHERE idea_id = ?",
                (json.dumps(route), idea_id),
            )
            store.conn.commit()
            next_job = (
                enqueue_concept(store, idea_id)
                if develop
                else enqueue_build_one(store, idea_id)
            )
        return {"idea_id": idea_id, "job_id": next_job, "develop": develop}

    @app.get("/api/ideas/{idea_id}/detail")
    def api_idea_detail(idea_id: str, _: str = Depends(require_user)):
        """Everything the create journey UI needs to draw one post."""
        from .brand import load_brand
        from .images import list_variants, render_mode_for_idea

        brand = load_brand()
        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
            jobs = {
                kind: store.active_job_for(idea_id, kind)
                for kind in (
                    "build_one", "render", "render_slide", "revise",
                    "concept", "takes",
                )
            }
            # The latest fan-out round (any status) so a resumed journey can
            # land back on the takes screen with its options intact.
            takes_row = store.conn.execute(
                "SELECT job_id, status FROM jobs WHERE idea_id = ? AND "
                "kind = 'takes' ORDER BY job_id DESC LIMIT 1",
                (idea_id,),
            ).fetchone()
            # Only report an error if the LATEST attempt failed — an old
            # failure that was retried successfully is not news.
            last_job = store.conn.execute(
                "SELECT kind, status, error FROM jobs WHERE idea_id = ? "
                "ORDER BY job_id DESC LIMIT 1",
                (idea_id,),
            ).fetchone()
            err_row = last_job if last_job and last_job["status"] == "ERROR" else None
        route = json.loads(idea.route_json) if idea.route_json else {}
        slides = json.loads(idea.slides_json) if idea.slides_json else []
        paths = json.loads(idea.asset_paths_json) if idea.asset_paths_json else []
        assets = []
        for p in paths:
            f = Path(p)
            assets.append(
                {"name": f.name, "mtime": int(f.stat().st_mtime) if f.exists() else 0}
            )
        return {
            "idea_id": idea.idea_id,
            "status": idea.status.value,
            "concept_note": idea.concept_note,
            "hook": idea.hook,
            "hook_options": route.get("hook_options", []),
            "caption": idea.caption,
            "comment_trigger": idea.comment_trigger,
            "hashtags": json.loads(idea.hashtags) if idea.hashtags else [],
            "slides": slides,
            "qa": route.get("qa", {}),
            "style": route.get("style", ""),
            "throughline": route.get("throughline", ""),
            "psych": route.get("psych", {}),
            "scroll_verdict": route.get("scroll_verdict", {}),
            "concept_gate": route.get("concept_gate", {}),
            "engagement_play": route.get("engagement_play", ""),
            "take": route.get("take", {}),
            "last_takes_job": dict(takes_row) if takes_row else None,
            "design_system": route.get("design_system", {}),
            "render_mode": render_mode_for_idea(idea, brand),
            "concept_brief": route.get("concept_brief"),
            "mechanic": (route.get("mechanic_lock") or {}).get("name")
            or route.get("mechanic", ""),
            # Per-slide charge % on an Amp post (empty for every other post) —
            # the review page's charge rail reads this.
            "charge_arc": charges_for_route(route, len(slides)) or [],
            "assets": assets,
            "variants": {
                str(k): v for k, v in list_variants(idea, settings, brand=brand).items()
            },
            "scheduled_for": (
                idea.scheduled_for.isoformat() if idea.scheduled_for else None
            ),
            "exported": bool(idea.exported_at),
            "active_jobs": {
                k: {
                    "job_id": j["job_id"],
                    "status": j["status"],
                    "progress": j["progress"],
                    "note": _job_note(j),
                    # The concept gate's live stage list, so the journey can show
                    # which check is running instead of an opaque spinner.
                    "gate_stages": _job_result(j).get("gate_stages") or [],
                }
                for k, j in jobs.items()
                if j
            },
            "last_error": (
                {"kind": err_row["kind"], "error": err_row["error"]} if err_row else None
            ),
        }

    @app.post("/api/ideas/{idea_id}/hook")
    def api_pick_hook(
        idea_id: str, hook: str = Form(...), _: str = Depends(require_user)
    ):
        """Set the winning hook (from hook_options or hand-written)."""
        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
            prev = idea.status
            store.save_build(idea_id, {"hook": hook.strip()})
            if prev != Status.done:
                store.set_status(idea_id, prev)  # save_build stamps done
        return {"idea_id": idea_id, "hook": hook.strip()}

    @app.post("/api/ideas/{idea_id}/build")
    def api_build_idea(idea_id: str, _: str = Depends(require_user)):
        """(Re)run the engine for this one idea, as a background job."""
        with _store(settings) as store:
            _idea_or_404(store, idea_id)
            job_id = enqueue_build_one(store, idea_id)
        return {"job_id": job_id, "kind": "build_one", "idea_id": idea_id}

    @app.post("/api/ideas/{idea_id}/revise")
    def api_revise_idea(
        idea_id: str, focus: str = Form(...), _: str = Depends(require_user)
    ):
        """Punch it up: one focused rewrite targeting a weak QA metric."""
        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
            if not idea.slides_json:
                raise HTTPException(400, "nothing built yet — build first")
            job_id = enqueue_revise(store, idea_id, focus=focus.strip() or "overall")
        return {"job_id": job_id, "kind": "revise", "idea_id": idea_id}

    @app.post("/api/ideas/{idea_id}/slides/{n}/regenerate")
    def api_regen_slide(
        idea_id: str,
        n: int,
        dry_run: bool = False,
        variants: int | None = None,
        _: str = Depends(require_user),
    ):
        """New paid background(s) for one slide (0-based index)."""
        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
            if not idea.slides_json:
                raise HTTPException(400, "nothing built yet — build first")
            job_id = enqueue_render_slide(
                store, idea_id, slide=n, dry_run=dry_run, variants=variants
            )
        return {"job_id": job_id, "kind": "render_slide", "idea_id": idea_id, "slide": n}

    @app.post("/api/ideas/{idea_id}/slides/{n}/recompose")
    def api_recompose_slide(idea_id: str, n: int, _: str = Depends(require_user)):
        """Re-overlay current copy on the saved background — free, synchronous."""
        from .images import ImageError, recompose_slide

        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
        try:
            result = recompose_slide(idea, n, settings)
        except ImageError as exc:
            raise HTTPException(400, str(exc))
        return {"idea_id": idea_id, "slide": n, "path": result.path}

    @app.post("/api/ideas/{idea_id}/slides/{n}/variant")
    def api_pick_variant(
        idea_id: str, n: int, variant: str = Form(...), _: str = Depends(require_user)
    ):
        """Promote a slide-background option to the canonical slide."""
        from .images import ImageError, pick_variant

        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
        try:
            path = pick_variant(idea, n, variant.strip(), settings)
        except ImageError as exc:
            raise HTTPException(400, str(exc))
        return {"idea_id": idea_id, "slide": n, "path": path}

    # --- discovery radar (moments = UK now · evergreen = worth knowing ·
    #     trending = formats to ride · ragebait = arguments worth starting) ----

    def _discover_kind(kind: str) -> str:
        if kind not in ("moments", "evergreen", "trending", "ragebait"):
            raise HTTPException(400, f"unknown discover kind '{kind}'")
        return kind

    @app.get("/api/moments")
    def api_moments(kind: str = "moments", _: str = Depends(require_user)):
        """Latest completed scan for one lane + whether one is running."""
        kind = _discover_kind(kind)
        with _store(settings) as store:
            done = store.conn.execute(
                "SELECT job_id, result_json, updated_at FROM jobs "
                "WHERE kind = ? AND status = 'COMPLETED' "
                "ORDER BY job_id DESC LIMIT 1",
                (kind,),
            ).fetchone()
            active = store.conn.execute(
                "SELECT job_id FROM jobs WHERE kind = ? "
                "AND status IN ('QUEUED','PROCESSING') ORDER BY job_id DESC LIMIT 1",
                (kind,),
            ).fetchone()
            # The newest job's state is the truth: if the last attempt ERRORed,
            # the journey should say so, not fall through to "came back empty".
            latest = store.conn.execute(
                "SELECT status, error FROM jobs WHERE kind = ? "
                "ORDER BY job_id DESC LIMIT 1",
                (kind,),
            ).fetchone()
        payload = {
            "job_id": None,
            "moments": [],
            "updated_at": None,
            "age_hours": None,
            "scanning": bool(active),
            "last_error": (
                latest["error"] if latest and latest["status"] == "ERROR" else None
            ),
        }
        if done:
            data = json.loads(done["result_json"] or "{}")
            payload["job_id"] = done["job_id"]
            payload["moments"] = data.get("moments", [])
            payload["updated_at"] = done["updated_at"]
            try:
                age = datetime.now().astimezone() - datetime.fromisoformat(
                    done["updated_at"]
                )
                payload["age_hours"] = round(age.total_seconds() / 3600, 1)
            except (TypeError, ValueError):
                pass
        return payload

    @app.get("/api/todays-pick")
    def api_todays_pick(_: str = Depends(require_user)):
        """Today's single strongest pick from the already-warmed lane scans
        (Bet 3). Concept/text only — reuses scans, renders nothing, no spend.
        Never 500s the create screen: any failure degrades to 'warming'."""
        from .todayspick import todays_pick

        with _store(settings) as store:
            try:
                return todays_pick(store, settings)
            except Exception:  # noqa: BLE001 — the card is a bonus, never a blocker
                return {"status": "warming", "pick": None, "alternates": []}

    @app.get("/api/shows")
    def api_shows(_: str = Depends(require_user)):
        """The five recurring shows the create screen opens on.

        Config-driven (config/shows/*.toml) so adding or retiring a show is a
        file, not a deploy of new code."""
        from .shows import ordered_shows

        return {
            "shows": [
                {
                    "key": s.key, "label": s.label, "tagline": s.tagline,
                    "blurb": s.blurb, "icon": s.icon, "weekday": s.weekday,
                    "kpi": s.kpi, "front_screen": s.front_screen,
                    "slides_min": s.slides_min, "slides_max": s.slides_max,
                }
                for s in ordered_shows()
            ]
        }

    @app.get("/api/concepts")
    def api_concepts_cached(
        max_age_hours: float = 8.0, show: str = "",
        _: str = Depends(require_user)
    ):
        """Today's concept set WITHOUT spending or waiting, when we already have
        one: the last completed set if it's still fresh, otherwise whatever run
        is already in flight so the screen can poll it instead of stacking a
        second one. This is what makes a reopened /create paint instantly."""
        from .concepts import cached_concepts

        with _store(settings) as store:
            cached = cached_concepts(store, max_age_hours=max_age_hours,
                                     show=show)
            if cached:
                return {"status": "ready", **cached}
            from .shows import job_show_filter

            clause, params = job_show_filter(show)
            active = store.conn.execute(
                "SELECT job_id FROM jobs WHERE kind = 'concepts' "
                "AND status IN ('QUEUED','PROCESSING') "
                "AND COALESCE(params_json,'{}') LIKE '%\"seed\": \"\"%' "
                + clause +
                "ORDER BY job_id DESC LIMIT 1",
                params,
            ).fetchone()
        return {
            "status": "thinking" if active else "cold",
            "concepts": [],
            "job_id": int(active["job_id"]) if active else None,
        }

    @app.post("/api/concepts")
    def api_concepts(
        seed: str = Form(""), fresh: bool = Form(False), show: str = Form(""),
        _: str = Depends(require_user),
    ):
        """Kick the fast concept sketch on the worker and return a job_id to poll.

        Stage 1 only — five concepts at headline level, on the fast model. It
        still runs as a job (not inline in this request) so a slow day can't
        have the proxy drop the connection; the create screen polls
        /api/jobs/{id} for the concepts in result_json."""
        from .worker import enqueue_concepts

        with _store(settings) as store:
            job_id = enqueue_concepts(store, seed=seed, fresh=fresh, show=show)
        return {"job_id": job_id, "kind": "concepts"}

    @app.post("/api/concepts/develop")
    def api_concept_develop(
        flavour: str = Form(""), title: str = Form(""), hook: str = Form(""),
        angle: str = Form(""), source: str = Form(""), seed: str = Form(""),
        amp: bool = Form(False),
        _: str = Depends(require_user),
    ):
        """Stage 2: drill one sketched concept down into a full brief. Runs on
        the creative model in the background; the card polls /api/jobs/{id}."""
        from .worker import enqueue_concept_detail

        concept = {
            "flavour": flavour, "title": title, "hook": hook,
            "angle": angle, "source": source,
            # An Amp card develops against Amp's persona and charge-cycle arc,
            # not the generic brief.
            "amp": amp or flavour == "amp",
        }
        if not (title.strip() or angle.strip()):
            raise HTTPException(400, "nothing to develop")
        with _store(settings) as store:
            job_id = enqueue_concept_detail(store, concept, seed=seed)
        return {"job_id": job_id, "kind": "concept_detail"}

    @app.post("/api/jobs/moments")
    def api_job_moments(
        count: int = Form(6), kind: str = Form("moments"),
        territories: str = Form(""), live_wire: bool = Form(False),
        _: str = Depends(require_user),
    ):
        """Kick a discovery scan.

        `live_wire` puts it on the interest-territory model (D9) instead of a
        country-wide trend scan; `territories` narrows that to a comma-separated
        selection. Without either, the lane scans exactly as it always has."""
        from .worker import enqueue_discover

        kind = _discover_kind(kind)
        picked = [t for t in (territories or "").split(",") if t.strip()]
        terr = picked if picked else ([] if live_wire else None)
        with _store(settings) as store:
            job_id = enqueue_discover(store, kind, count=count, territories=terr)
        return {"job_id": job_id, "kind": kind}

    @app.get("/api/canon")
    def api_canon_get(_: str = Depends(require_user)):
        """THE MULTIVERSE's canon — the whole editable document (D16)."""
        from .series import load_canon

        with _store(settings) as store:
            return load_canon(store).model_dump()

    @app.put("/api/canon")
    def api_canon_put(payload: dict, _: str = Depends(require_user)):
        """Replace the canon. The operator authors the storyline; the engine
        proposes the next episode from whatever this says."""
        from .series import Canon, save_canon

        try:
            canon = Canon.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — a bad edit is a 400, not a 500
            raise HTTPException(400, f"canon didn't validate: {exc}") from None
        with _store(settings) as store:
            save_canon(store, canon)
        return canon.model_dump()

    @app.post("/api/canon/episode")
    def api_canon_episode(
        change: str = Form(...), unresolved: str = Form(""),
        title: str = Form(""), cast: str = Form(""), idea_id: str = Form(""),
        _: str = Depends(require_user),
    ):
        """Record what an episode changed, so the next one can extend it."""
        from .series import record_episode

        with _store(settings) as store:
            episode = record_episode(
                store, change=change, unresolved=unresolved, title=title,
                cast=[c for c in cast.split(",") if c.strip()], idea_id=idea_id,
            )
        return episode.model_dump()

    @app.post("/api/canon/reset")
    def api_canon_reset(confirm: str = Form(""), _: str = Depends(require_user)):
        """The hard reset (D16): wipe the world and start clean.

        Requires an explicit confirmation because it is total and
        irreversible — season, episodes, standings and notes all go."""
        from .series import reset_canon

        if confirm.strip().upper() != "RESET":
            raise HTTPException(400, "type RESET to confirm — this wipes the whole canon")
        with _store(settings) as store:
            return reset_canon(store).model_dump()

    @app.get("/api/roster")
    def api_roster(_: str = Depends(require_user)):
        from .roster import load_roster, load_world

        world = load_world()
        return {
            "world": world.model_dump(),
            "characters": [
                {"key": c.key, "name": c.name, "cls": c.cls, "what": c.what,
                 "trait": c.trait, "role": c.role, "protected": c.protected}
                for c in load_roster().values()
            ],
        }

    @app.get("/api/territories")
    def api_territories(_: str = Depends(require_user)):
        """LIVE WIRE's in-season interest territories, heaviest first."""
        from .territories import in_season

        return {
            "territories": [
                {"key": t.key, "label": t.label, "weight": t.weight}
                for t in in_season()
            ]
        }

    @app.post("/api/jobs/meta")
    def api_job_meta(_: str = Depends(require_user)):
        """Manually refresh the live-meta research (auto-refreshes weekly)."""
        from .worker import _active_meta_scan

        with _store(settings) as store:
            active = _active_meta_scan(store)
            job_id = active["job_id"] if active else store.create_job("meta_scan")
        return {"job_id": job_id, "kind": "meta_scan"}

    @app.post("/api/moments/{job_id}/dig")
    def api_moment_dig(
        job_id: int, moment: int = Form(...), _: str = Depends(require_user)
    ):
        """Dig into one broad moment → a job scouting its specific headlines."""
        from .worker import enqueue_moment_detail

        with _store(settings) as store:
            job = store.get_job(job_id)
            if job is None or job["kind"] not in ("moments", "evergreen", "trending"):
                raise HTTPException(404, "no such discovery scan")
            moments = json.loads(job["result_json"] or "{}").get("moments", [])
            if not 0 <= moment < len(moments):
                raise HTTPException(400, "moment index out of range")
            topic = moments[moment].get("title", "")
            dig_job = enqueue_moment_detail(store, topic=topic)
        return {"job_id": dig_job, "kind": "moment_detail", "topic": topic}

    @app.post("/api/moments/{job_id}/angles")
    def api_moment_angles(
        job_id: int, moment: int = Form(...), _: str = Depends(require_user)
    ):
        """Stage-2 of a two-stage scan: write the angles for one surfaced story.

        Returns the angles inline if the scan already carried them (a legacy
        full scan or a cached result); otherwise queues a `lane_angles` job."""
        with _store(settings) as store:
            job = store.get_job(job_id)
            if job is None or job["kind"] not in (
                "moments", "evergreen", "trending", "ragebait", "moment_detail",
            ):
                raise HTTPException(404, "no such discovery scan")
            moments = json.loads(job["result_json"] or "{}").get("moments", [])
            if not 0 <= moment < len(moments):
                raise HTTPException(400, "moment index out of range")
            m = moments[moment]
            if m.get("angles"):
                return {"angles": m["angles"], "cached": True}
            aid = store.create_job(
                "lane_angles",
                params={
                    "lane": job["kind"], "title": m.get("title", ""),
                    "why": m.get("why", ""), "count": 3,
                },
            )
        return {"job_id": aid, "kind": "lane_angles"}

    @app.post("/api/moments/{job_id}/use")
    def api_moments_use(
        job_id: int,
        moment: int = Form(...),
        angle: int = Form(-1),
        custom: str = Form(""),
        style: str = Form(""),
        length: str = Form(""),
        scheduled_for: str = Form(""),
        develop: bool = Form(False),
        takes: bool = Form(False),
        _: str = Depends(require_user),
    ):
        """Build from a moment — a preset angle, or the editor's own take.

        Pass `custom` with your own angle to override the preset `angle`.
        Pass `takes` to fan out to competing takes before the full write.
        """
        when = _parse_when(scheduled_for)
        with _store(settings) as store:
            job = store.get_job(job_id)
            if job is None or job["kind"] not in (
                "moments", "evergreen", "trending", "ragebait", "moment_detail",
            ):
                raise HTTPException(404, "no such discovery scan")
            moments = json.loads(job["result_json"] or "{}").get("moments", [])
            if not 0 <= moment < len(moments):
                raise HTTPException(400, "moment index out of range")
            m = moments[moment]
            if custom.strip():
                # The editor's own angle on this moment.
                note = custom.strip()
            else:
                angles = m.get("angles", [])
                if not 0 <= angle < len(angles):
                    raise HTTPException(400, "pick an angle or write your own")
                a = angles[angle]
                note = a.get("concept_note", a.get("title", ""))
                if a.get("hook"):
                    note += f" — open with: {a['hook']}"
            # Trends, ragebait and moments seed differently for the learning
            # loop and for the build framing (event = the moment is the star;
            # trend = the format is the vehicle to follow faithfully;
            # ragebait = the argument is the star and the post picks a side).
            kind_tag = (
                job["kind"] if job["kind"] in ("trending", "ragebait") else "moment"
            )
            idea = _new_seed(
                store,
                concept_note=f"[{m.get('title', 'UK moment')}] {note}",
                content_category=kind_tag,
                style=style,
                length=length,
                scheduled_for=when,
                # The full moment/trend travels with the seed so the build
                # keeps it central instead of drifting back to product talk.
                moment={
                    k: v
                    for k, v in {
                        "kind": kind_tag,
                        "title": m.get("title", ""),
                        "why": m.get("why", ""),
                        "when": m.get("when", ""),
                        "peak": m.get("peak", ""),
                        "category": m.get("category", ""),
                        "angle": note,
                    }.items()
                    if v
                },
            )
            # Moments decay fast — stamp it so the calendar can nag.
            store.conn.execute(
                "UPDATE ideas SET decay_speed = ?, learning_tag = ? WHERE idea_id = ?",
                (
                    m.get("decay_speed", "days"),
                    f"{kind_tag}:{m.get('category', 'news')}",
                    idea.idea_id,
                ),
            )
            store.conn.commit()
            if takes:
                from .worker import enqueue_takes

                next_job = enqueue_takes(store, idea.idea_id)
            elif develop:
                from .worker import enqueue_concept

                next_job = enqueue_concept(store, idea.idea_id)
            else:
                next_job = enqueue_build_one(store, idea.idea_id)
        return {
            "idea_id": idea.idea_id, "job_id": next_job,
            "develop": develop, "takes": takes,
        }

    # --- concept development ("develop it with me") ----------------------------

    @app.post("/api/ideas/{idea_id}/develop")
    def api_develop(
        idea_id: str, feedback: str = Form(""), _: str = Depends(require_user)
    ):
        """One development round; feedback is the editor's plain-words input."""
        from .worker import enqueue_concept

        with _store(settings) as store:
            _idea_or_404(store, idea_id)
            job_id = enqueue_concept(store, idea_id, feedback=feedback.strip())
        return {"job_id": job_id, "kind": "concept", "idea_id": idea_id}

    @app.post("/api/ideas/{idea_id}/brief")
    async def api_save_brief(
        idea_id: str, request: Request, _: str = Depends(require_user)
    ):
        """Save the editor's direct edits to the concept brief."""
        form = await request.form()
        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
            route = json.loads(idea.route_json) if idea.route_json else {}
            brief = route.get("concept_brief", {})
            for key in ("angle", "hook_direction", "tone", "visual_direction"):
                if key in form:
                    brief[key] = str(form[key])
            outline = [
                str(form[f"outline_{i}"])
                for i in range(MAX_SLIDES)
                if f"outline_{i}" in form
            ]
            if outline:
                brief["outline"] = outline
            route["concept_brief"] = brief
            store.conn.execute(
                "UPDATE ideas SET route_json = ? WHERE idea_id = ?",
                (json.dumps(route), idea_id),
            )
            store.conn.commit()
        return {"idea_id": idea_id, "brief": brief}

    # --- manual posting (download photos + copy caption) -----------------------

    @app.get("/api/ideas/{idea_id}/manual")
    def api_manual_post(idea_id: str, _: str = Depends(require_user)):
        """Everything needed to post by hand: the images + one caption block.

        The caption keeps its natural line breaks (the TikTok app allows them
        when you type; only the bulk-CSV API strips them), then a blank line,
        then the hashtags — ready to paste in one go.
        """
        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
        paths = json.loads(idea.asset_paths_json) if idea.asset_paths_json else []
        images = []
        for i, p in enumerate(paths):
            src = Path(p)
            images.append(
                {
                    "url": f"/media/{idea_id}/{src.name}",
                    "filename": f"{idea_id}_slide_{i + 1}{src.suffix or '.jpg'}",
                }
            )
        caption = (idea.caption or "").strip()
        hashtags = json.loads(idea.hashtags) if idea.hashtags else []
        tag_str = " ".join(
            t if t.startswith("#") else f"#{t}" for t in hashtags if t
        ).strip()
        caption_text = caption + (f"\n\n{tag_str}" if tag_str else "")
        # The comment section's opening move is part of the post — pin the
        # engine's comment trigger as the first comment the moment it's live.
        first_comment = (idea.comment_trigger or "").strip()
        return {
            "idea_id": idea_id,
            "caption_text": caption_text,
            "first_comment": first_comment,
            "images": images,
            "count": len(images),
            # The 20-second in-app steps that actually move reach and can't be
            # done from the CSV — sound especially (Photo Mode posts with a
            # trending sound outperform silent ones, and we can't see live audio).
            "checklist": [
                "Add a trending sound in the TikTok app before posting — browse "
                "the Trending tab in the sound picker and pick something "
                "low-vocal. Photo Mode posts with trending audio outperform "
                "silent ones.",
                "Pin the first comment (copied above) right after posting.",
                "Reply to the first few comments within the hour — early replies "
                "feed the same signals that get you out of the test pool.",
            ],
        }

    # --- learning loop (real results → future builds) --------------------------

    @app.post("/api/ideas/{idea_id}/metrics")
    def api_log_metrics(
        idea_id: str,
        views: int = Form(0),
        likes: int = Form(0),
        comments: int = Form(0),
        shares: int = Form(0),
        saves: int = Form(0),
        _: str = Depends(require_user),
    ):
        """Log a post's real TikTok numbers — the fuel for the learning loop."""
        from .learning import METRIC_FIELDS

        values = {"views": views, "likes": likes, "comments": comments,
                  "shares": shares, "saves": saves}
        if any(v < 0 for v in values.values()):
            raise HTTPException(400, "metrics can't be negative")
        metrics = {f: values[f] for f in METRIC_FIELDS}
        metrics["logged_at"] = datetime.now(timezone.utc).isoformat()
        with _store(settings) as store:
            idea = _idea_or_404(store, idea_id)
            # Keep any one-tap rating already set.
            existing = json.loads(idea.metrics_json) if idea.metrics_json else {}
            if existing.get("rating"):
                metrics["rating"] = existing["rating"]
            store.set_metrics(idea_id, metrics)
        return {"idea_id": idea_id, "metrics": metrics}

    @app.post("/api/ideas/{idea_id}/rate")
    def api_rate(idea_id: str, rating: str = Form(...), _: str = Depends(require_user)):
        """One-tap result: 🔥 hit / 😐 meh / 💀 flop — near-zero friction so it
        actually gets logged. Merges, so it never wipes any views entered."""
        from .learning import RATINGS

        rating = rating.strip().lower()
        if rating not in RATINGS:
            raise HTTPException(400, f"rating must be one of {sorted(RATINGS)}")
        with _store(settings) as store:
            _idea_or_404(store, idea_id)
            store.merge_metrics(
                idea_id,
                {"rating": rating, "rated_at": datetime.now(timezone.utc).isoformat()},
            )
        return {"idea_id": idea_id, "rating": rating}

    @app.get("/api/insights")
    def api_insights(_: str = Depends(require_user)):
        """What's actually working for this account, from logged results."""
        from .learning import MIN_POSTS_FOR_NOTES, insights

        with _store(settings) as store:
            digest = insights(store)
        digest["feeding_engine"] = digest["posts_logged"] >= MIN_POSTS_FOR_NOTES
        return digest

    @app.get("/api/shows/performance")
    def api_show_performance(_: str = Depends(require_user)):
        """How each show is doing, judged on its OWN KPI.

        Without this the five-show plan is a guess that never resolves: after
        a couple of months this is what tells you which to double down on and
        which to kill."""
        from .learning import show_scoreboard

        with _store(settings) as store:
            return show_scoreboard(store)

    @app.post("/api/metrics/import")
    async def api_metrics_import(
        request: Request,
        file: UploadFile = File(...),
        _: str = Depends(require_user),
    ):
        """Bulk-log results from a TikTok analytics CSV export (Content tab →
        Download data), matched to posts by caption. Fills the learning loop in
        one go instead of hand-typing every post."""
        from .analytics import import_tiktok_csv

        raw = await file.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        with _store(settings) as store:
            return import_tiktok_csv(store, text)

    # --- scheduling + calendar ------------------------------------------------

    @app.post("/api/ideas/{idea_id}/schedule")
    def api_schedule(
        idea_id: str, when: str = Form(""), _: str = Depends(require_user)
    ):
        """Set (ISO datetime) or clear (empty) the user-chosen posting slot."""
        parsed = _parse_when(when)
        with _store(settings) as store:
            _idea_or_404(store, idea_id)
            store.set_schedule(idea_id, parsed)
        return {
            "idea_id": idea_id,
            "scheduled_for": parsed.isoformat() if parsed else None,
        }

    def _card(idea: Idea) -> dict:
        """Compact calendar-card payload for one idea."""
        paths = json.loads(idea.asset_paths_json) if idea.asset_paths_json else []
        thumb = Path(paths[0]).name if paths else None
        stale = False
        if idea.decay_speed and not idea.exported_at:
            shelf = _DECAY_SHELF_DAYS.get(idea.decay_speed.value)
            if shelf is not None:
                created = idea.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                stale = datetime.now(timezone.utc) - created > timedelta(days=shelf)
        views = None
        rating = None
        if idea.metrics_json:
            try:
                m = json.loads(idea.metrics_json)
                views = int(m.get("views") or 0) or None
                rating = m.get("rating")
            except (json.JSONDecodeError, TypeError, ValueError):
                views = None
        # Nudge the learning loop off starvation: a post that's likely live
        # (exported, or its scheduled slot has passed) and is rendered but not
        # yet rated wants a one-tap outcome. Without this the loop silently
        # starves at small-account scale.
        sched = idea.scheduled_for
        if sched is not None and sched.tzinfo is None:
            sched = sched.replace(tzinfo=timezone.utc)
        posted_ish = bool(idea.exported_at) or (
            sched is not None and sched < datetime.now(timezone.utc)
        )
        needs_rating = bool(paths) and rating is None and posted_ish
        return {
            "idea_id": idea.idea_id,
            "label": idea.hook or idea.concept_note,
            "status": idea.status.value,
            "built": bool(idea.slides_json),
            "rendered": bool(paths),
            "exported": bool(idea.exported_at),
            "thumb": thumb,
            "stale": stale,
            "decay": idea.decay_speed.value if idea.decay_speed else None,
            "views": views,
            "rating": rating,
            "needs_rating": needs_rating,
            "scheduled_for": (
                idea.scheduled_for.isoformat() if idea.scheduled_for else None
            ),
        }

    @app.get("/api/calendar")
    def api_calendar(
        request: Request,
        date_from: str = "",
        date_to: str = "",
        _: str = Depends(require_user),
    ):
        """Scheduled posts grouped by day, plus the unscheduled tray."""
        try:
            start = (
                datetime.fromisoformat(date_from)
                if date_from
                else datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            )
            end = (
                datetime.fromisoformat(date_to)
                if date_to
                else start + timedelta(days=42)
            )
        except ValueError:
            raise HTTPException(400, "bad date range")

        days: dict[str, list[dict]] = {}
        tray: list[dict] = []
        with _store(settings) as store:
            for idea in store.ideas_scheduled_between(start, end):
                key = idea.scheduled_for.date().isoformat()
                days.setdefault(key, []).append(_card(idea))
            # Tray: built posts with no date yet — visible ammunition.
            for idea in store.list_ideas():
                if idea.scheduled_for or idea.exported_at:
                    continue
                if idea.status in (Status.done, Status.review) and idea.slides_json:
                    tray.append(_card(idea))
        return {
            "from": start.date().isoformat(),
            "to": end.date().isoformat(),
            "days": days,
            "tray": tray,
        }

    @app.post("/api/export/range")
    def api_export_range(
        request: Request,
        date_from: str = Form(...), date_to: str = Form(...), _: str = Depends(require_user)
    ):
        """Calendar 'export week': Metricool CSV for posts scheduled in-range."""
        try:
            start = datetime.fromisoformat(date_from)
            end = datetime.fromisoformat(date_to) + timedelta(days=1)  # inclusive
        except ValueError:
            raise HTTPException(400, "bad date range")
        publisher = _metricool_publisher(request)
        with _store(settings) as store:
            result = publisher.export(store, settings, date_from=start, date_to=end)
        return {
            "exported": result.exported_ids,
            "skipped": result.skipped,
            "csv_path": result.csv_path,
        }

    @app.post("/api/jobs/{job_id}/retry")
    def api_job_retry(job_id: int, _: str = Depends(require_user)):
        """Re-enqueue a failed job with the same kind, idea and params."""
        retryable = {
            "build", "build_one", "render", "render_slide",
            "angles", "revise", "concept", "trends", "moments", "evergreen",
            "trending", "ragebait", "takes", "meta_scan", "run",
        }
        with _store(settings) as store:
            job = store.get_job(job_id)
            if job is None:
                raise HTTPException(404, "no such job")
            if job["status"] != "ERROR":
                raise HTTPException(400, "only failed jobs can be retried")
            if job["kind"] not in retryable:
                raise HTTPException(400, f"'{job['kind']}' jobs can't be retried here")
            new_id = store.create_job(
                job["kind"],
                idea_id=job["idea_id"],
                params=json.loads(job["params_json"] or "{}"),
            )
        return {"job_id": new_id, "kind": job["kind"], "retried_from": job_id}

    @app.get("/api/jobs")
    def api_jobs(_: str = Depends(require_user)):
        with _store(settings) as store:
            return store.list_jobs()

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: int, _: str = Depends(require_user)):
        with _store(settings) as store:
            job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job

    @app.post("/api/export")
    def api_export(
        request: Request,
        week: bool = True, limit: int | None = None, _: str = Depends(require_user),
    ):
        publisher = _metricool_publisher(request)
        if limit is None and week:
            limit = publisher.cols.schedule.per_day * 7
        with _store(settings) as store:
            result = publisher.export(store, settings, limit=limit)
        return {
            "exported": result.exported_ids,
            "skipped": result.skipped,
            "csv_path": result.csv_path,
        }

    @app.get("/api/runs")
    def api_runs(_: str = Depends(require_user)):
        with _store(settings) as store:
            rows = store.conn.execute(
                "SELECT * FROM runs ORDER BY run_id DESC LIMIT 50"
            ).fetchall()
            total = store.conn.execute(
                "SELECT COALESCE(SUM(spend_usd),0) AS s FROM runs"
            ).fetchone()["s"]
        return {"total_spend_usd": float(total), "runs": [dict(r) for r in rows]}

    # --- media + downloads (path-safe) --------------------------------------

    @app.get("/media/{idea_id}/{filename}")
    def media(idea_id: str, filename: str, _: str = Depends(require_user)):
        return FileResponse(_safe_output_path(settings, idea_id, filename))

    @app.get("/download/ready/{filename}")
    def download_ready(filename: str, _: str = Depends(require_user)):
        return FileResponse(_safe_output_path(settings, "ready", filename))

    @app.get("/download/assets.zip")
    def download_assets_zip(_: str = Depends(require_user)):
        import io
        import zipfile

        from fastapi.responses import StreamingResponse

        ready = Path(settings.output_dir) / "ready"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if ready.exists():
                for f in ready.iterdir():
                    if f.is_file():
                        zf.write(f, arcname=f.name)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=chrgd_ready.zip"},
        )

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"ok": True})

    return app
