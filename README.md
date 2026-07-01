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

## Status — Milestones 1–2 ✅

The build ships incrementally (see [Build order](#build-order)).

- **M1 — skeleton:** SQLite backlog store, `capture`, config/secrets scaffolding.
- **M2 — pipeline runner:** turns a seed row into a validated finished-post JSON
  via the **OpenAI API**, honouring the QA gate and logging spend.

Implemented now:

- `chrgd capture` — split a rough dump into deduped, queued seed rows.
- `chrgd backlog` — inspect the backlog.
- `chrgd build` — run the 6-stage content engine over N queued ideas (OpenAI).
- `chrgd review` — inspect posts the QA gate flagged.

The remaining commands are registered as stubs and report which milestone fills them.

> **Provider:** this build uses **OpenAI** (chat for the pipeline, `gpt-image-1`
> for carousel backgrounds in M3) — one key covers everything. The model is
> configurable via `CHRGD_OPENAI_MODEL`.

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
```

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
content_engine_prompt.md   # the CHRGD Content Engine reasoning (loaded by the pipeline, M2+)
config/        # runtime config (e.g. metricool_columns.toml, M4)
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
3. Carousel image builder — background gen + Pillow overlay + safe zones + JPEG export. ← *next*
4. Metricool CSV export + `ready/` folder. *(Milestones 1–4 are the whole win.)*
5. Trend scout writing seed rows.
6. Video builder (Higgsfield image-to-video + ffmpeg + draft/auto audio).
7. Orchestration + cron + cost-guard polish; optional `unified_api` / `tiktok_direct`.
