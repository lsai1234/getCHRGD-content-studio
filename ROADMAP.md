# CHRGD Content Studio — Roadmap

The plan to build the whole thing: a hosted website at
`contentstudio.getchrgd.co.uk` with the content engine behind it. Milestones
1–4 (the core loop) are **done**. Higgsfield video **infrastructure is now in
place but switched off** — enabling it later is a small, contained step.

Sizing: **S** ≈ half a session · **M** ≈ one session · **L** ≈ multiple sessions.

---

## ✅ Done

| # | Milestone | Ships |
|---|---|---|
| 1 | Skeleton + SQLite backlog + `capture` | `chrgd capture`, `chrgd backlog` |
| 2 | Pipeline runner (OpenAI) + QA gate | `chrgd build`, `chrgd review` |
| 3 | Carousel image builder (gpt-image-2) | `chrgd render` — slide 1 `high`, rest `medium` |
| 4 | Metricool CSV export | `chrgd export` (+ `--sample`) |
| — | **Higgsfield video infra (OFF)** | feature flag, provider client skeleton, `jobs` table, guarded CLI path |

60-ish tests, all offline. The weekly loop already replaces the daily grind.

### What "video infra, off" means concretely
- `CHRGD_VIDEO_ENABLED=false` master switch (config + `.env.example`).
- `chrgd/video.py`: `VideoProvider` interface + `HiggsfieldProvider` skeleton
  (submit→poll→download lifecycle, Segmind base-URL override), all guarded by
  the flag. Calling any of it while off raises a clear `VideoDisabledError`.
- `jobs` table + store methods for resumable async state (reused by the web
  worker too) — so a future video job can never double-bill after a crash.
- `chrgd render` on a `video` post routes to the video path and reports it's
  disabled, rather than rendering a carousel.
- **No paid video code runs, and none is callable, until M6 flips it on.**

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

### A2 — Background jobs · **M**
- `build` / `render` (and later `video`) run off the request via a lightweight
  in-process worker driven by the **`jobs` table** (already built). Resumable,
  no Redis/Celery.
- Live progress in the UI via HTMX polling.

### A3 — Deploy early · **M**
Get A1+A2 live on the box before building all the screens, so you test on real
infrastructure.
- **VPS:** ~£4/mo (Hetzner/DigitalOcean), Ubuntu, always-on.
- **Runtime:** uvicorn under **systemd**; **Caddy** for automatic HTTPS.
- **DNS:** one `A` record `contentstudio` → the VPS IP (see note below).
- `.env` on the box (`chmod 600`); `ffmpeg` + fonts installed for later.
- Nightly backup of the SQLite file + `output/`.

### A4 — The screens · **M–L**
Backlog · Build queue · **Review & approve** (slide previews, edit copy,
re-render) · Export (generate CSV, download) · Trends · Dashboard (runs, spend,
schedule).

---

## Phase B — Content-engine additions

### B1 — M5 Trend scout · **S–M**
OpenAI web search → UK-gym topical signals → seed rows with `decay_speed` +
ship-fast priority. Filter for brand fit + claim safety. State the limit (no
live TikTok in-app sounds). Wire the `chrgd trends` stub + a UI button.

### B2 — M7 Orchestration & cost-guard polish · **M**
- `chrgd run --count N`: trend-scout → pick → build → render → export.
- Scheduled runs via systemd timer / APScheduler.
- Per-run pre-estimate + hard spend cap spanning build+render(+video); structured
  logging; the `runs` table already records built/spent/exported.

---

## Phase C — Turn on video (M6)

Everything here plugs into infra that already exists.
- Implement `HiggsfieldProvider.submit/poll/download` against the live API;
  reconcile request/response shapes.
- Orchestrate via the `jobs` table: submit → poll `QUEUED/PROCESSING/COMPLETED/
  FAILED/ERROR` → download, with retries/timeouts, **resumable, no re-bill**.
  Reuse one seed/Soul mode for character consistency.
- **ffmpeg assembly:** stitch clips in slide order, burn text overlays (same
  safe zones), add audio bed.
- **Audio modes:** `auto` (neutral bed, schedulable) + `draft` (visuals only,
  add trending sound in-app). Trend-driven → `draft`.
- Flip `CHRGD_VIDEO_ENABLED=true`; extend the exporter's video column (already
  scaffolded).
- **Needs from you:** a Higgsfield (or Segmind) key + an audio-bed source.

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
**A1 → A2 → A3 (deploy) → A4 → B1 → B2 → C (video) → D.**
Ship the website around carousels first; fold video in via Phase C with no rework.

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
