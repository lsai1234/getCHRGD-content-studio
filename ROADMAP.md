# CHRGD Content Studio — Roadmap (work remaining)

Where we are and everything left, in build order. Milestones 1–4 (the core
"daily win": capture → build → render → export) are **done and pushed**. What
follows is the rest of the brief (M5–M7) plus the **web dashboard** and
**VPS deployment** you asked for, which sit on top of the brief.

Sizing: **S** ≈ half a session · **M** ≈ one session · **L** ≈ multiple sessions.

---

## ✅ Done — Milestones 1–4

| # | Milestone | Ships |
|---|---|---|
| 1 | Skeleton + SQLite backlog + `capture` | `chrgd capture`, `chrgd backlog` |
| 2 | Pipeline runner (OpenAI) + QA gate | `chrgd build`, `chrgd review` |
| 3 | Carousel image builder (gpt-image-2) | `chrgd render` (slide 1 high, rest medium) |
| 4 | Metricool CSV export | `chrgd export` (+ `--sample`) |

47 tests, all offline. The weekly loop already replaces the daily grind.

---

## 🔜 Remaining brief milestones

### M5 — Trend scout · **S–M**
Scheduled web-search job for UK-gym-relevant *topical/cultural* signals, then
seed rows with `decay_speed` + ship-fast priority.
- Provider: OpenAI web search (`web_search` tool) — one key, consistent.
- Filter for brand fit + claim safety (prompt rules) before seeding.
- **State the limit** in output: no live TikTok in-app sounds/hashtags.
- Wire the existing `chrgd trends` stub.
- **Depends on:** nothing. Feeds the backlog, so useful before or after the UI.

### M6 — Video builder (Higgsfield) · **L**
For `post_type = video`:
- Higgsfield image-to-video (Bearer token; aggregator base-URL override, e.g.
  Segmind). Feed slide background + motion prompt from `visual_intent`.
- **Async job lifecycle:** submit → `generation_id` → poll
  `QUEUED/PROCESSING/COMPLETED/FAILED/ERROR` → download on complete. Retries,
  timeouts, **resumable via a `jobs` table so a crash never re-bills**.
- Reuse one seed / Soul mode across clips for character consistency.
- Assemble with **ffmpeg**: stitch in slide order, burn text overlays (same
  safe zones), add audio bed.
- **Audio modes:** `auto` (licensed/neutral bed, ready to schedule) and
  `draft` (visuals only, flagged to add a trending sound in-app). Trend-driven
  videos default to `draft`.
- Export one vertical mp4 to `output/<idea_id>/`; extend the exporter's video
  column (already scaffolded in `publisher.py`).
- **Needs from you:** a Higgsfield (or Segmind) API key, and a decision on the
  audio-bed source.

### M7 — Orchestration, cron & cost-guard polish · **M**
- `chrgd run --count N`: chain trend-scout → pick N → build → render → export.
- Scheduling: APScheduler or a documented cron/systemd-timer sample.
- **Cost guard polish:** per-run pre-estimate + hard cap spanning build+render
  (+video), abort before exceeding; log spend per run (the `runs` table exists).
- Structured logging across the app.
- **Optional posting backends (behind flags), later:**
  - `unified_api` — Postpeer/Ayrshare-style hands-off posting.
  - `tiktok_direct` — TikTok Content Posting API (OAuth, 24h refresh, unaudited
    = SELF_ONLY, per-post `privacy_level`, default draft/self-only).

---

## 🌐 Web dashboard (your end goal) — **L**, phased

A browser UI at `contentstudio.getchrgd.co.uk` over exactly the CLI actions.
Backend reuses every existing module (`Store`, `pipeline`, `images`,
`publisher`) — the CLI and web share one engine.

**Stack:** FastAPI + server-rendered templates (Jinja + HTMX). One process, no
build step, cheap on a tiny VPS. (A React SPA is possible but heavier for no
real gain here.)

### Phase W1 — Backend API + auth · **M**
- FastAPI app wrapping the engine; endpoints for backlog list/CRUD, capture,
  build, render, review/approve, export, download CSV/assets, runs/spend.
- **Single-user login** (session cookie + password hash in `.env`). Non-negotiable
  before it's on the public internet — your API keys sit behind it.
- SQLite in **WAL mode** for concurrent web + worker access.

### Phase W2 — Background jobs · **M**
- `build`/`render`/`video` are slow → run them off the request via a
  lightweight in-process worker driven by a **`jobs` table** (survives restarts,
  resumable, aligns with the brief's "track job state in SQLite"). No Redis/Celery.
- Live status/progress in the UI (HTMX polling).

### Phase W3 — The screens · **M–L**
- **Backlog:** table, capture box, priority/category edit, void.
- **Build queue:** pick N, watch progress, see QA pass/flag.
- **Review & approve:** slide previews (served from `output/`), edit copy,
  re-render, approve → eligible for export.
- **Export:** pick the week, generate CSV, download CSV + assets zip; show what
  was scheduled.
- **Trends:** run the scout, one-click seed the strong ones.
- **Dashboard:** runs, spend this week/month, what's scheduled.

---

## 🚀 Deployment — VPS + Caddy · **M**

- **Box:** ~£4/mo VPS (Hetzner CX22 / DigitalOcean), Ubuntu. Always-on so cron
  + long video jobs + the site all work; SQLite and `output/` live on its disk.
- **Runtime:** uvicorn/gunicorn under **systemd**; **Caddy** reverse proxy for
  automatic HTTPS on the subdomain.
- **DNS:** an `A` record `contentstudio` → the VPS IP (you'll add this at your
  domain registrar; I'll give exact values).
- **System deps:** `ffmpeg` (for M6), fonts (DejaVu present; add brand fonts).
- **Secrets:** `.env` on the box, `chmod 600`, never committed.
- **Backups:** nightly `rsync`/`litestream` of the SQLite file + `output/` (a
  few pennies of object storage, or another disk). Cheap insurance.
- **Scheduled runs:** systemd timer (or cron) calling `chrgd run` weekly and
  `chrgd trends` on its own cadence.

---

## 🔒 Cross-cutting / before "trust it in production"

- **Live paid-path test (you):** a real `chrgd build` + `chrgd render` with your
  `OPENAI_API_KEY`. I can't exercise paid calls here — the fake-client + dry-run
  tests prove the plumbing, not the live API shapes. First run is the real proof.
- **gpt-image-2 pricing (you/me):** the spend-log cost table
  (`images._IMAGE_COST`) is an estimate; confirm gpt-image-2's per-image prices
  and I'll set them exactly. (Verify gpt-image-2 is the exact model id on your
  account — if it differs, it's one env var.)
- **Metricool columns (you):** download Metricool's template, run
  `chrgd export --sample`, reconcile `config/metricool_columns.toml`. ~10 min.
- **Pipeline model choice (you):** default is `gpt-4o`; tell me if you want a
  specific quality/cost tier.
- **Security review:** before the site is public — auth, input validation, rate
  limiting, asset-path traversal, no key leakage in logs/UI.

---

## Recommended order from here

1. **M5 trend scout** (small, feeds the backlog) — optional quick win.
2. **Web W1 + W2** (API, auth, background jobs) — the skeleton of your site.
3. **Deployment** — get W1/W2 live on the VPS at the subdomain early, so you're
   testing on the real box, not just locally.
4. **Web W3 screens** — flesh out the UI against the deployed backend.
5. **M6 video** + **M7 orchestration** — additive; slot in once carousels are
   humming through the website.

> Milestones 1–4 are the win; 5–7 and the web layer are additive. We can ship
> the website around carousels only, and fold video in later without rework.

## Decisions I need from you to proceed

- **Next step:** start the **web app (W1)**, or do **M5 trend scout** first?
- **Video (M6):** are you getting a **Higgsfield/Segmind** key, or should we
  ship the website carousel-only for now and add video later?
- **Domain:** can you add a DNS record at your registrar when we deploy? (I'll
  hand you the exact `A` record.)
