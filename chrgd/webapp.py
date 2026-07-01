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
from .models import Status
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

    # --- dashboard (HTML) ---------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, _: str = Depends(require_user_page)):
        with _store(settings) as store:
            ideas = store.list_ideas()
            counts = {
                "queued": store.count(Status.queued),
                "done": store.count(Status.done),
                "review": store.count(Status.review),
                "total": store.count(),
            }
            row = store.conn.execute(
                "SELECT COALESCE(SUM(spend_usd),0) AS s FROM runs"
            ).fetchone()
            spend = float(row["s"])
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "user": current_user(request),
                "ideas": ideas,
                "counts": counts,
                "spend": spend,
                "flash": request.query_params.get("flash"),
            },
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
        return RedirectResponse(f"/?flash={flash}", status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request, _: str = Depends(require_user_page)):
        with _store(settings) as store:
            flagged = store.list_ideas(status=Status.review)
        rows = "".join(
            f"<tr><td>{i.idea_id}</td><td>{i.concept_note}</td>"
            f"<td><a href='/api/ideas/{i.idea_id}'>json</a> · "
            f"<form method='post' action='/api/ideas/{i.idea_id}/approve' "
            f"style='display:inline'><button style='margin:0;padding:4px 10px'>"
            f"Approve</button></form></td></tr>"
            for i in flagged
        )
        body = (
            "<h1>Needs review</h1><table><tr><th>ID</th><th>Concept</th><th></th></tr>"
            + (rows or "<tr><td colspan=3 class=muted>Nothing flagged.</td></tr>")
            + "</table>"
        )
        return HTMLResponse(
            _wrap_page(templates, request, current_user(request), body)
        )

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

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"ok": True})

    return app


def _wrap_page(templates, request, user, inner_html: str) -> str:
    """Render base.html with a raw inner body (used for simple pages)."""
    tpl = templates.get_template("base.html")
    # base.html defines a {% block body %}; render via a tiny child string.
    child = "{% extends 'base.html' %}{% block body %}" + inner_html + "{% endblock %}"
    return templates.env.from_string(child).render(request=request, user=user)
