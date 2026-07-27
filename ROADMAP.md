# CHRGD Content Studio — Roadmap

The plan to build the whole thing: a hosted website at
`contentstudio.getchrgd.co.uk` with the content engine behind it. Milestones
1–4 (the core loop) are **done**. The Higgsfield image-to-video pipeline (M6) is
now **built but switched off by default** — flip `CHRGD_VIDEO_ENABLED=true` with a
key to turn it on; no paid video call runs while the flag is off.

Sizing: **S** ≈ half a session · **M** ≈ one session · **L** ≈ multiple sessions.

---

## ✅ Done

| # | Milestone | Ships |
|---|---|---|
| 1 | Skeleton + SQLite backlog + `capture` | `chrgd capture`, `chrgd backlog` |
| 2 | Pipeline runner (OpenAI) + QA gate | `chrgd build`, `chrgd review` |
| 3 | Carousel image builder (gpt-image-2) | `chrgd render` — slide 1 `high`, rest `low` (see `brand.toml [generation]`) |
| 4 | Metricool CSV export | `chrgd export` (+ `--sample`) |
| 6 | **Higgsfield image-to-video (built, OFF)** | `chrgd render` on a `video` post + `POST /api/jobs/video/{id}` → a stitched 9:16 reel |

Tests all offline. The weekly loop already replaces the daily grind.

### What "video built, off" means concretely
- `CHRGD_VIDEO_ENABLED=false` master switch (config + `.env.example`). While off,
  every entry point raises a clear `VideoDisabledError`; no paid call is callable.
- `chrgd/video.py`: `HiggsfieldProvider` (submit→poll→download, Segmind base-URL
  override) + ffmpeg assembly + a resumable `render_video` orchestrator.
- `jobs` table + store methods for resumable async state (per-clip ids + paths),
  its own worker lane, and startup requeue — so a video job never double-bills
  after a crash.
- Turn it on: set the flag + `HIGGSFIELD_API_KEY`, install `ffmpeg`, and
  reconcile the documented API shapes against the live account (see Phase C).

---

## Phase A — Web foundation (your end goal)

A FastAPI backend + server-rendered UI (Jinja + HTMX) over the *same* engine
the CLI uses. One process, no build step, cheap on a small VPS.

### A1 — Backend API + single-user auth · **M**
- FastAPI wrapping `Store` / `pipeline` / `images` / `publisher`.
- Endpoints: backlog list/CRUD, capture, build, render, review/approve, export,
  download CSV + assets, runs/spend.
- **Login** (session cookie + password hash in `.env`) — required before it's on
  the public internet; your API keys sit behind it.
- SQLite switched to **WAL mode** for safe concurrent web + worker access.

### A2 — Background jobs · **DONE ✅**
- `build` / `render` (and later `video`) run off the request via a single
  in-process worker driven by the **`jobs` table**. Resumable (interrupted
  paid work is failed, not re-billed), no Redis/Celery.
- Live progress in the dashboard via JS polling (`/api/jobs`).

### A3 — Deploy · **DONE ✅ (artifacts ready — run on your VPS)**
Turnkey deploy kit in `deploy/` + step-by-step [`DEPLOY.md`](DEPLOY.md):
- **VPS:** ~£4/mo (Hetzner CX22/DigitalOcean), Ubuntu, always-on.
- **Runtime:** uvicorn under **systemd** (single process — the worker is a
  singleton); **Caddy** for automatic Let's Encrypt HTTPS.
- **DNS:** one Cloudflare `A` record `contentstudio` → the VPS IP.
- `deploy/setup.sh` bootstraps the box; `.env` `chmod 600`; ffmpeg installed.
- Nightly `deploy/chrgd-backup.sh` (WAL-safe DB snapshot + asset tarball).
- **Left to do (needs you):** provision the VPS + run the kit (§1–6 of DEPLOY.md).

### A4 — The screens · **DONE ✅**
Dashboard (counts, spend, scheduled posts, recent runs) · Backlog (capture,
filter, void, render) · Build queue (build control + live job polling) ·
**Review & approve** (slide-thumbnail previews, editable hook/slides/caption/
hashtags, re-render, approve) · Export (run export, download CSV + assets zip,
see what's scheduled). Trends screen arrives with B1.

---

## Phase B — Content-engine additions

### B1 — M5 Trend scout · **DONE ✅**
OpenAI web search (Responses API) → ranked UK-gym topical hooks, filtered for
brand fit + claim safety, seeded as rows with `decay_speed` + ship-fast
priority (days→P1). States the limit (no live TikTok in-app sounds). Shipped:
`chrgd trends [--seed]`, a `trends` background job kind, and a Trends screen
(run scout, view ranked hooks, one-click "seed all").

### B3 — Create & Schedule journeys · **DONE ✅**
The `UPGRADE_PROPOSAL.md` build: `/create` (facts / idea / blank-canvas doors,
mechanic gallery, 3 hook options, QA scorecard + targeted "punch it up"
revisions, in-journey image generation with slide-1 variants, free text
re-lay from saved backgrounds, phone preview) and `/calendar` (month view,
drag-drop rescheduling, unscheduled tray, decay staleness flags, "+ create
for this day", export-week). Nav collapsed to Create · Calendar · Library.
New job kinds: `build_one`, `angles`, `revise`, `render_slide`. Style presets
+ prompt composition (style · slide brief · consistency · no-text) in
`brand.toml`; mechanics in `config/mechanics.toml`.
**Still needs you: the live paid image test** — one real `render` with your
OpenAI key to confirm the exact image model id and real per-image cost
(`_IMAGE_COST` in `chrgd/images.py` is an estimate).

### B2 — M7 Orchestration & cost-guard polish · **DONE ✅**
- `chrgd run --count N [--scout] [--dry-run] [--no-export]`: chains
  [scout] → build → render → export; also a `run` background job kind + a
  "Run full chain" control on the Build screen.
- Scheduled runs via `deploy/chrgd-run.{service,timer}` (weekly systemd timer),
  documented in DEPLOY.md.
- Cost guard: one run-wide spend cap across build+render; a single combined
  `runs` row (no double-counting), and image spend is now logged too (via
  `services.render_idea`). Structured logging (`chrgd/logging_setup.py`) on
  CLI + web.

---

## Phase C — Video (M6) — ✅ built, feature-flagged off

Implemented in `chrgd/video.py` + wired through the worker/CLI/web:
- `HiggsfieldProvider.submit/poll/download` against the documented image2video
  lifecycle (base-URL override supports the Segmind aggregator). Request/response
  field names are isolated in `_submit_payload` / `_parse_*` helpers — the one
  place to reconcile against the live API when you add a key.
- Orchestrated via the `jobs` table: submit → poll `QUEUED/PROCESSING/COMPLETED/
  FAILED/ERROR` → download, with a poll timeout, **resumable and no re-bill**
  (per-clip ids + downloaded paths persist on the job; a crash requeues and
  resumes). One `seed` is reused across every slide for character consistency.
- **ffmpeg assembly:** normalise each clip to 9:16 → concat in slide order →
  optional audio bed. No text burn-in — this engine bakes copy into the slide
  artwork at image-gen time, so clips already carry their text.
- **Audio modes:** `auto` (neutral bed under the reel) + `draft` (silent, add
  trending sound in-app). Trend-driven → `draft`.
- Own worker lane (`VIDEO_KINDS`) so minutes of polling never delay a build.
- Trigger: `chrgd render <id>` on a `video` post, or `POST /api/jobs/video/{id}`.

**Still needs from you to go live:**
- A Higgsfield (or Segmind) API key + reconcile the request/response shapes in
  `video.py` against the live API (they follow the docs but are unverified).
- `ffmpeg` on the host (`apt-get install ffmpeg`) — the assembly step shells out
  to the binary.
- An audio-bed file for `auto` mode (`CHRGD_VIDEO_AUDIO_BED`).
- Extend the exporter's video column when you want reels in the Metricool CSV.

---

## Phase D — Optional, later

- `unified_api` publisher (Postpeer/Ayrshare) for hands-off posting, behind a flag.
- `tiktok_direct` (TikTok Content Posting API: OAuth, 24h refresh, unaudited =
  SELF_ONLY, per-post `privacy_level`, default draft/self-only).

---

## Cross-cutting / before "trust it in production"
- **Live paid-path test (you):** a real `build` + `render` with your OpenAI key.
  The fake-client + dry-run tests prove the plumbing, not the live API shapes.
- **gpt-image-2 pricing:** the spend-log cost table is an estimate; confirm real
  per-image prices (and that `gpt-image-2` is the exact model id on your account).
- **Metricool columns:** `chrgd export --sample`, diff against Metricool's
  template, edit `config/metricool_columns.toml`. ~10 min.
- **Pipeline model:** default `gpt-4o`; tell me if you want a different tier.
- **Security review** before the site is public: auth, input validation, rate
  limiting, asset-path traversal, no key leakage.

---

## Suggested order
**A1 ✅ → A2 ✅ → A3 ✅ (deploy kit) → A4 ✅ → B1 ✅ → B2 ✅ → C ✅ (video) → D.**
The full web app, trend scout, orchestration, and the M6 video pipeline are
built. Next: get it live on the VPS (DEPLOY.md), then flip video on when you have
a Higgsfield key + ffmpeg on the host (reconcile the API shapes at that point).

---

## About that DNS record (Q3 — plain-English)
A domain name like `getchrgd.co.uk` is just a friendly label; the internet needs
a numeric **IP address** to actually find a server. **DNS** is the phone book
that maps names → IPs, and an **"A record"** is one entry in it.

To put the app at `contentstudio.getchrgd.co.uk`, we add one A record at
whoever manages your domain (the registrar — e.g. GoDaddy, Namecheap, Cloudflare,
123-Reg):

> **Type:** A  **Name/Host:** `contentstudio`  **Value:** `<the VPS's IP>`

That's the whole job — a 2-minute form in your registrar's dashboard. When we
deploy I'll give you the exact IP to paste in; Caddy on the server then fetches
the HTTPS certificate automatically, so `https://contentstudio.getchrgd.co.uk`
just works. **You don't need to understand DNS beyond "paste one line when I
give it to you."** The only thing I need to confirm: you can log in to wherever
`getchrgd.co.uk` was bought/managed. If you're not sure who that is, tell me the
registrar or forward me the domain purchase email and I'll point you to the exact
screen.
