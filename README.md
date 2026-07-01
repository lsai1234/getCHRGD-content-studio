# CHRGD Content Studio

A local, self-runnable content system for the UK gym/supplement brand **CHRGD**.
It turns a backlog of ideas into import-ready TikTok/Instagram **carousels** (and,
later, short videos) and hands them to a scheduler as a **Metricool-compatible CSV
+ `ready/` asset folder** — so the only daily step is gone.

It replaces an old n8n workflow. The content *reasoning* lives in
`content_engine_prompt.md` (the "CHRGD Content Engine" instructions); this repo is
the app built around it: storage, generation, assembly, and a scheduler-ready export.

> **Last-mile reality:** we don't auto-post to TikTok. Metricool (an audited TikTok
> partner) bulk-publishes carousels + video across TikTok/Instagram/Shorts. This
> system's job ends at producing clean assets + a CSV to bulk-import once a week.

## Status — Milestones 1–4 ✅  *(the whole daily win is done)*

The build ships incrementally (see [Build order](#build-order)).

- **M1 — skeleton:** SQLite backlog store, `capture`, config/secrets scaffolding.
- **M2 — pipeline runner:** seed row → validated finished-post JSON via the
  **OpenAI API**, honouring the QA gate and logging spend.
- **M3 — carousel image builder:** a background per slide from the image API,
  then approved text overlaid *in code* with Pillow inside the TikTok safe
  zones; JPEG/WebP, ≤1080p, five slides to `output/<idea_id>/`.
- **M4 — Metricool CSV export:** the primary deliverable — a bulk-import CSV +
  matching `output/ready/` asset folder, config-driven columns, idempotent.

Implemented now:

- `chrgd capture` — split a rough dump into deduped, queued seed rows.
- `chrgd backlog` — inspect the backlog.
- `chrgd build` — run the 6-stage content engine over N queued ideas (OpenAI).
- `chrgd review` — inspect posts the QA gate flagged.
- `chrgd render` — generate the five carousel images for a built post.
- `chrgd export` — write the Metricool CSV + `ready/` folder for the week.

The remaining commands (`trends`, `run`) are stubs reporting their milestone.

**The weekly loop now:** `capture` → `build` → `render` → `export`, then
bulk-import one CSV into Metricool. That's the daily grind gone.

> **Provider:** this build uses **OpenAI** (chat for the pipeline, `gpt-image-2`
> for carousel backgrounds) — one key covers everything. Models are configurable
> via `CHRGD_OPENAI_MODEL` / `CHRGD_IMAGE_MODEL`. Per-slide image quality is set
> in `brand.toml`: slide 1 renders `high`, slides 2–5 `medium`.

> **What's left:** see [`ROADMAP.md`](ROADMAP.md) for M5–M7 (trends, video,
> orchestration), the rest of the web dashboard, and VPS deployment.

### Web dashboard (Phase A1 ✅)

A FastAPI app wraps the same engine behind a single-user login.

```bash
pip install -e ".[web]"
# set a password (preferred: a hash) + a session key in .env:
python -m chrgd.webauth 'your-password'          # prints CHRGD_WEB_PASSWORD_HASH
#   CHRGD_WEB_PASSWORD_HASH=pbkdf2_sha256$...     (or CHRGD_WEB_PASSWORD=plaintext)
#   CHRGD_SECRET_KEY=<long random string>
chrgd serve                                        # http://127.0.0.1:8000
```

Ships now: session login/logout, a dashboard (counts, spend, capture form,
backlog), a review page with approve, a JSON API (`/api/backlog`, `/api/capture`,
`/api/render/{id}`, `/api/export`, `/api/runs`, …), and path-safe asset serving
(`/media/...`). **Build/render run as background jobs** (Phase A2) via an
in-process worker over the `jobs` table — the dashboard live-polls `/api/jobs`.
Richer screens land in A4. **Higgsfield video infra is present but off.**

**Deploying it:** a turnkey kit lives in [`deploy/`](deploy/) (systemd unit,
Caddyfile, backup + bootstrap scripts) with a step-by-step guide in
[`DEPLOY.md`](DEPLOY.md) — get it live at `contentstudio.getchrgd.co.uk` on a
~£4/mo VPS with automatic HTTPS.

> **Deployment (agreed plan):** engine first (M2–4), then a FastAPI web
> dashboard + backend, hosted on a small always-on VPS with Caddy for automatic
> HTTPS at `contentstudio.getchrgd.co.uk`. SQLite, assets, and cron all live on
> the one box. Not built yet — tracked after the engine milestones.

## Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,llm]"   # llm pulls in the OpenAI SDK (needed for `build`)
cp .env.example .env          # set OPENAI_API_KEY to run `build`
```

## Usage

```bash
# Capture ideas — one idea per line, or split on ";".
chrgd capture "gym bros who hog the squat rack for 40 mins
pre-workout that makes you feel invincible; then you nap"

# Attach shared seed metadata:
chrgd capture "heatwave gym sessions" --category humour --priority 1 --decay days

# Inspect the backlog:
chrgd backlog
chrgd backlog --status queued

# Build finished posts from queued ideas (needs OPENAI_API_KEY in .env):
chrgd build --count 3
chrgd build --count 3 --dry-run   # runs the LLM, skips paid image/video (M3+)

# Inspect anything the QA gate flagged:
chrgd review
chrgd review --show G-0001        # full built JSON for one idea

# Render the five carousel images for a built post:
chrgd render G-0001               # calls the image API (gpt-image-1)
chrgd render G-0001 --dry-run     # branded placeholder backgrounds, no spend

# Export the week's rendered posts to a Metricool bulk-import CSV:
chrgd export --sample             # sample CSV to diff against Metricool's template
chrgd export --week               # CSV + ready/ folder; marks rows exported
```

`render` generates a background per slide, then overlays the approved headline
and supporting text in code (Pillow) inside the safe zones — reliable text every
time, no model-rendered gibberish. Styling lives in `brand.toml`.

`export` selects `done` + rendered posts, builds one CSV row each (schedule,
target networks, caption with hashtags inline and line breaks stripped, media
references), copies assets into `output/ready/`, and stamps rows `exported_at`
so nothing exports twice. Column headers and filename-vs-URL media style are
read from `config/metricool_columns.toml` — run `chrgd export --sample` and diff
its headers against the template you download from Metricool, then edit the TOML.

`build` calls OpenAI to run the six-stage engine, validates the JSON against the
`Post` model, and applies the QA thresholds from `content_engine_prompt.md`
(overall/hook/visual_originality ≥ 8, plus one engagement gate ≥ 8). A post that
misses is re-requested once, then flagged `review` rather than shipped. Spend is
estimated per run and recorded in the `runs` table; `CHRGD_MAX_SPEND_PER_RUN`
caps a run before it exceeds budget.

Capture splits on newlines / `;`, strips list bullets, dedupes against existing
rows (case-insensitive on the concept note), and writes queued seed rows. LLM-assisted
splitting of mushy paragraphs arrives in a later milestone; until then line breaks
control separation.

## Layout

```
chrgd/
  cli.py       # typer CLI (capture/backlog/build/review live; rest stubbed)
  config.py    # pydantic-settings, loaded from .env
  db.py        # SQLite store — ideas + runs tables, idempotent methods
  models.py    # Idea / Post / Slide models, status enums, QA gate
  capture.py   # dump → seed rows (LLM-free)
  pipeline.py  # OpenAI runner: seed row → validated post JSON + QA gate
  brand.py     # brand.toml loader — canvas, safe zones, colours, fonts
  images.py    # carousel builder: background gen + Pillow text overlay
  publisher.py # Metricool CSV export + ready/ folder (Publisher backends)
content_engine_prompt.md   # the CHRGD Content Engine reasoning (loaded by the pipeline)
brand.toml     # carousel styling (fonts, CHRGD orange/yellow, safe zones)
config/
  metricool_columns.toml   # Metricool column mapping (edit to match your plan)
data/          # SQLite db (gitignored)
output/        # generated assets (gitignored)
```

## The backlog store

One `ideas` table, two logical record types:

- **Seed fields** (set on capture): `idea_id`, `status`, `priority`,
  `content_category`, `target_viewer`, `pain_point`, `core_tension`,
  `concept_note`, `learning_tag`, `decay_speed`, `created_at`.
- **Build fields** (set on generation, M2+): `post_type`, `hook`, `slides_json`,
  `caption`, `comment_trigger`, `hashtags`, `route_json`, `asset_paths_json`,
  `scheduled_for`, `processed_at`, `exported_at`, platform URLs.

Every write is idempotent — a re-run must not duplicate rows or re-bill. A `runs`
table records what each pipeline run built, spent, and exported (used from M2+).

## Configuration

All config comes from the environment / `.env` (see `.env.example`). Secrets are
never hardcoded. Milestone 1 only touches `CHRGD_DB_PATH`, `CHRGD_OUTPUT_DIR`, and
`CHRGD_ID_PREFIX`; API keys are read by later milestones.

## Tests

```bash
pytest
```

## Build order

Each milestone stops for testing before the next begins.

1. ~~Skeleton + SQLite backlog + `capture` + `.env.example`.~~ ✅
2. ~~Pipeline runner → validated post JSON from a seed row (LLM only).~~ ✅
3. ~~Carousel image builder — background gen + Pillow overlay + safe zones + JPEG export.~~ ✅
4. ~~Metricool CSV export + `ready/` folder.~~ ✅ *(Milestones 1–4 are the whole win.)*
5. Trend scout writing seed rows. ← *next (or start the web dashboard)*
6. Video builder (Higgsfield image-to-video + ffmpeg + draft/auto audio).
7. Orchestration + cron + cost-guard polish; optional `unified_api` / `tiktok_direct`.
