"""Web dashboard — Phase A1: FastAPI backend + single-user auth.

Wraps the same engine the CLI uses (Store / pipeline / images / publisher)
behind a session login. This phase ships auth, a minimal dashboard, the JSON
API for the core actions, and safe asset downloads. Long-running build/render
move to a background worker in Phase A2; richer screens come in A4.

Run it:  `chrgd serve`  (installs: `pip install -e '.[web]'`).
"""

from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, get_settings
from .db import Store
from .models import Idea, Status
from .webauth import auth_configured, verify_credentials
from .worker import Worker, enqueue_build, enqueue_render

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


def create_app(settings: Settings | None = None, *, run_worker: bool = True) -> FastAPI:
    settings = settings or get_settings()
    from .logging_setup import configure_logging

    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if run_worker:
            worker = Worker(settings)
            worker.start()
            app.state.worker = worker
        try:
            yield
        finally:
            if worker:
                worker.stop()

    app = FastAPI(title="CHRGD Content Studio", lifespan=lifespan)
    app.state.settings = settings
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key or secrets.token_hex(32),
        session_cookie="chrgd_session",
        same_site="lax",
        https_only=False,  # Caddy terminates TLS; set true if serving TLS direct
    )

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
        ctx.update(
            request=request,
            user=current_user(request),
            active=active,
            review_count=review_count,
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
        pinned = (
            json.loads(idea.pinned_comments_json)
            if idea.pinned_comments_json
            else []
        )
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
            "pinned_comments": pinned,
            "hashtags": " ".join(hashtags),
            "slides": slides,
            "format": route.get("format", ""),
            "qa": route.get("qa", {}),
            "assets": assets,
        }

    # --- pages (HTML) -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, _: str = Depends(require_user_page)):
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
        from .publisher import get_publisher

        publisher = get_publisher("metricool_csv")
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
        from .publisher import get_publisher

        publisher = get_publisher("metricool_csv")
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
            for i, slide in enumerate(slides):
                if f"slide_headline_{i}" in form:
                    slide["headline"] = form[f"slide_headline_{i}"]
                if f"slide_supporting_{i}" in form:
                    slide["supporting"] = form[f"slide_supporting_{i}"]

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
            if "pinned_comments" in form:
                # One pinned comment per line in the textarea.
                pinned = [
                    line.strip()
                    for line in str(form["pinned_comments"]).splitlines()
                    if line.strip()
                ]
                fields["pinned_comments_json"] = json.dumps(pinned)
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
        from .images import ImageError, render_carousel

        with _store(settings) as store:
            idea = store.get_idea(idea_id)
            if idea is None:
                raise HTTPException(404, "no such idea")
            try:
                result = render_carousel(idea, settings, dry_run=dry_run)
            except ImageError as exc:
                raise HTTPException(400, str(exc))
            store.save_asset_paths(idea_id, result.paths)
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
    def api_export(week: bool = True, limit: int | None = None, _: str = Depends(require_user)):
        from .publisher import get_publisher

        publisher = get_publisher("metricool_csv")
        if limit is None and week:
            limit = publisher.cols.schedule.per_day * 7
        with _store(settings) as store:
            result = publisher.export(store, settings, limit=limit)
        return {
            "exported": result.exported_ids,
            "skipped": result.skipped,
            "csv_path": result.csv_path,
            "pinned_comments_path": result.pinned_comments_path,
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
