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
  zones; JPEG/WebP, ≤1080p, one image per slide to `output/<idea_id>/`.
- **M4 — Metricool CSV export:** the primary deliverable — a bulk-import CSV +
  matching `output/ready/` asset folder, config-driven columns, idempotent.

Implemented now:

- `chrgd capture` — split a rough dump into deduped, queued seed rows.
- `chrgd backlog` — inspect the backlog.
- `chrgd build` — run the 6-stage content engine over N queued ideas (OpenAI).
- `chrgd review` — inspect posts the QA gate flagged.
- `chrgd render` — generate the carousel images for a built post.
- `chrgd export` — write the Metricool CSV + `ready/` folder for the week.

- `chrgd trends` — scout current UK-gym topical hooks and (with `--seed`) queue them.
- `chrgd run` — the full chain: `[--scout] → build → render → export` (for cron).
- `chrgd campaign` — the launch campaign: where it is today, and seeding its
  written content into the backlog. See [`LAUNCH_PLAN.md`](LAUNCH_PLAN.md).

### The launch campaign

Every other config here describes a *format*; `config/campaign.toml` describes
a *moment*. It carries the launch date, four phases defined as day offsets from
it, and the CTA each phase is allowed to use — injected into every write call,
so a Tuesday STRAIGHT UP knows it's launch week without being re-briefed.

The shape: the pre-launch fortnight **sells nothing** (at ~70 followers a
countdown reaches nobody who cares) and spends itself on reach that plants the
question the quiz answers; launch week bolts one ask onto the same content —
the **quiz**, never "buy", with the domain written out in plain text rather
than "link in bio". 29 posts are written and ready in
`config/launch_backlog.toml`, carried by a new show (**THE STACK**) and six
cold-open mechanics.

```bash
chrgd campaign status                 # the phase, and this week's ask
chrgd campaign seed --all --dry-run   # the whole calendar, nothing written
chrgd campaign seed --phase prime     # queue the pre-launch run
```

Set `armed = false` (or let the window lapse) and the studio builds exactly
what it built before the campaign existed — the same pass-through guarantee the
show layer makes, asserted in `tests/test_campaign.py`.

**The weekly loop now:** `capture` → `build` → `render` → `export`, then
bulk-import one CSV into Metricool. That's the daily grind gone.

> **Provider:** this build uses **OpenAI** (chat for the pipeline, `gpt-image-2`
> for carousel backgrounds) — one key covers everything. Models are configurable
> via `CHRGD_OPENAI_MODEL` / `CHRGD_IMAGE_MODEL`. Per-slide image quality is set
> in `brand.toml`: slide 1 renders `high`, slides 2–5 `medium`.

> **What's left:** see [`ROADMAP.md`](ROADMAP.md) for M5–M7 (trends, video,
> orchestration), the rest of the web dashboard, and VPS deployment.

### The two journeys (Create & Schedule ✅)

The web app is organised around two linked journeys rather than a hub of
screens (see [`UPGRADE_PROPOSAL.md`](UPGRADE_PROPOSAL.md) for the thinking):

- **Create** (`/create`) — one flow from nothing to a finished carousel,
  built like a quiz: one focused screen at a time with a progress rail, a
  back button, and big tappable option cards that auto-advance — you flick
  through the choices instead of filling in forms. Six ways in: the two
  discovery lanes (**Happening now** / **Worth knowing**), **My own idea**
  (a rough one-liner), **From research** (paste facts — the engine proposes
  2–3 angles per fact and you pick), **A proven format** (mechanics from
  `config/mechanics.toml`), or **Fully manual**. Length is flexible and the
  engine's call — a shareable meme might be 1–2 slides, a classic carousel
  4–6, a deep-dive up to 10 — with an optional length nudge on the style
  screen and add/remove-slide controls in the editor. The engine returns **3 hook
  options** as tap-to-set cards and a visible **QA scorecard** with a
  per-metric *punch it up* revision button; the slide copy sits behind
  tap-to-expand accordions so amending anything is one tap away without
  slowing the happy path. Approving the copy generates the images
  in-journey: slide 1 gets multiple background **variants** to pick from,
  every slide has *Regenerate* (paid) and *Re-lay text* (free — backgrounds
  are kept on disk so copy edits never re-bill), and a phone-frame preview
  shows the TikTok safe zones. The final step is a mini-calendar plus
  quick-pick time chips: schedule it or bank it in the tray.
- **Calendar** (`/calendar`) — month view of everything scheduled. Drag cards
  between days to reschedule, drag to the tray to unschedule, **“＋ create
  for this day”** starts the Create journey with the date pre-filled, topical
  ideas that sit too long get a *going stale* flag, and **Export week** runs
  the Metricool CSV for exactly that week (posts keep the day/time you chose).

Visual style presets (gritty flash-photo, clean editorial, meme-adjacent,
dark cinematic) live in `brand.toml [styles]`; one is chosen per post and
appended to every slide's image prompt together with a set-consistency clause
and a strict no-text-in-image clause. The old screens (backlog, review queue,
batch build, trends, export) live on under **Library**.

A FastAPI app wraps the same engine behind a single-user login.

```bash
pip install -e ".[web]"
# set a password (preferred: a hash) + a session key in .env:
python -m chrgd.webauth 'your-password'          # prints CHRGD_WEB_PASSWORD_HASH
#   CHRGD_WEB_PASSWORD_HASH=pbkdf2_sha256$...     (or CHRGD_WEB_PASSWORD=plaintext)
#   CHRGD_SECRET_KEY=<long random string>
chrgd serve                                        # http://127.0.0.1:8000
```

Full screens behind a session login: **Dashboard** (counts, spend, scheduled
posts, recent runs), **Backlog** (capture, filter, void, render), **Build**
(build control + live job polling), **Review & approve** (slide-thumbnail
previews, editable copy, re-render, approve), **Export** (run export, download
CSV + assets zip). **Build/render run as background jobs** via an in-process
worker over the `jobs` table (the UI live-polls `/api/jobs`). A JSON API backs
every action, with path-safe asset serving (`/media/...`). **Higgsfield video
infra is present but off.**

**Deploying it:** a turnkey kit lives in [`deploy/`](deploy/) (systemd unit,
Caddyfile, backup + bootstrap scripts) with a step-by-step guide in
[`DEPLOY.md`](DEPLOY.md) — get it live at `contentstudio.getchrgd.co.uk` on a
~£4/mo VPS with automatic HTTPS.

> **Deployment (agreed plan):** engine first (M2–4), then a FastAPI web
> dashboard + backend, hosted on a small always-on VPS with Caddy for automatic
> HTTPS at `contentstudio.getchrgd.co.uk`. SQLite, assets, and cron all live on
> the one box. Not built yet — tracked after the engine milestones.

## Install

> **On Windows and not sure where to start?** Follow
> [`QUICKSTART_WINDOWS.md`](QUICKSTART_WINDOWS.md) — a copy-paste, no-jargon guide
> to run it and do a real test in ~10 minutes.

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

# Render the carousel images for a built post:
chrgd render G-0001               # calls the image API (gpt-image-1)
chrgd render G-0001 --dry-run     # branded placeholder backgrounds, no spend

# Export the week's rendered posts to a Metricool bulk-import CSV:
chrgd export --sample             # sample CSV to diff against Metricool's template
chrgd export --week               # CSV + ready/ folder; marks rows exported

# Clear out content past the retention window (the studio also does this daily):
chrgd prune --dry-run             # what the next sweep would take
chrgd prune                       # sweep now
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
  retention.py # the daily clear-out: content past its window, rows + images
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

### Retention — the library clears itself out

Every screen that reads the library (the calendar, the review wall, the create
screen's "used recently" nudges) reads rows carrying whole slide sets and prose
episodes, so a library that only ever grows makes the whole studio slower — and
the rendered images fill the disk alongside it.

`chrgd/retention.py` deletes content `CHRGD_RETENTION_DAYS` past its last
activity: the idea row and its `output/<id>/` images, plus stale `runs`/`jobs`
history and orphaned asset folders. An idea's
age is the newest of created / built / exported / scheduled, so a post scheduled
for next week is never "old". Nothing with a queued or running job is touched,
and a post you rated keeps its text row so the learning loop still has results to
steer builds with (`CHRGD_RETENTION_KEEP_RATED=false` to delete those too).

**It ships off (`CHRGD_RETENTION_DAYS=0`) on purpose** — the first sweep on a
studio that has been running for months deletes most of its library, and that is
not something an upgrade should do to you. Arm it once you have seen the number:

```bash
chrgd prune --dry-run       # exactly what the first sweep would take
# happy? set CHRGD_RETENTION_DAYS=30 in .env and restart
```

From then on the web app sweeps once a day on the research worker; Settings →
Storage shows the window and offers a preview or an immediate clear-out; `chrgd
prune` does the same from the CLI.

### Waiting — what runs at the same time

Nearly all the time the studio spends is spent waiting on somebody else's
server: 20-60s for an image, 30-90s for a write, a few seconds for each judge.
Most of those waits have nothing to do with each other, and the engine used to
take them strictly one after another anyway.

* **The carousel render.** Slide 1 is generated first and alone, because it is
  the anchor every other slide is generated against — that reference is what
  keeps one character, one palette and one look across the set. Every slide
  after it depends on slide 1 and on *nothing else*, so they now go together
  (`CHRGD_RENDER_CONCURRENCY`, default 4). At 25s an image a 6-slide carousel
  drops from ~3 minutes to ~1.5, and a 10-slide one from ~5 to ~2. Identical
  prompts, identical anchoring, identical spend. A **pan** swipe style is the
  exception and stays sequential: there each slide continues the previous one's
  edge, which is a real dependency, not an accident of the loop.
* **The fast job lane** runs two workers (`CHRGD_FAST_WORKERS`), so kicking a
  build no longer parks a render behind it for the whole write. Claiming a job
  is a guarded `UPDATE` built for exactly this, so the lane's workers share the
  queue without ever double-processing.
* **A batch build** writes `CHRGD_BUILD_CONCURRENCY` posts at once — each is an
  independent engine call. Persistence stays on the calling thread, in queue
  order; the spend cap is checked between waves.
* **The checks on a finished post** — claims, and likeness/continuity — read the
  same post, write nothing, and know nothing about each other, so they run
  together (`CHRGD_PARALLEL_GATES`).
* **One HTTP client per credential.** Six places built their own OpenAI client
  and the image path built a fresh one *per image*, so every call paid a DNS
  lookup and a TLS handshake before it could send a byte. They now share a
  warm connection pool (`chrgd/llm.py`).

The concept gate's four stages are deliberately *not* overlapped: each one
judges what the last one produced, and speculating on the glance test only pays
off when the score passes first time while spending a wasted judge call every
time it doesn't.

The learning loop reads a bounded corpus (the newest 500 rated posts, light
columns) rather than every rated post ever — that block is injected into every
build and every fan-out, and a rated post is kept forever by design.

### Where the credits go

The dashboard's **Where the credits go** table breaks the last 30 days of spend
down by stage (build, render, concept, takes, angles, revise, meta_scan) with a
per-run average, so an expensive stage is visible rather than buried in one
total.

Two things keep that bill down by construction. Calls that *write a post* get the
full engine prompt (`engine_base()`); calls that only sharpen or re-angle one
already-written headline get `voice_base()` — the brand voice and the UK spine,
about a sixth of the size. And the concept gate's sharpening loop keeps the
**best-scoring** version rather than the last one, stopping as soon as a rewrite
fails to beat what it replaced, so a round that isn't working costs one judge
call instead of a creative call plus a worse opener.

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
