# CHRGD Content Studio — Upgrade Proposal: Create & Schedule

**The pitch in one line:** turn the studio from a hub of admin screens into two
linked journeys — **Create** (facts / idea / blank canvas → a finished, genuinely
good TikTok carousel with real AI-generated images) and **Schedule** (a calendar
you drag posts onto) — so making great content and planning when it goes out is
one continuous flow.

---

## 1. Where we actually are (the honest diagnosis)

The engine works, but the *product* is a hub, not a journey:

- **Images aren't really being made.** The image code exists
  (`chrgd/images.py` calls OpenAI's image API, then overlays approved text with
  Pillow inside TikTok safe zones) — but it only runs as a separate, manual
  "render" step, the live paid path has never been exercised
  (flagged in `ROADMAP.md` as an untested cross-cutting item), and nothing in
  the flow pushes you through it. So in practice the studio produces *copy*,
  and the photos — the thing that actually stops the scroll — never happen.
- **Creation is scattered across five screens.** Backlog → Build → Review →
  (Render, buried as a button) → Export. Each screen is fine; the *journey*
  doesn't exist. There's no way to start from a fact, no way to start from a
  blank canvas, and no moment where you look at the finished slides as they'll
  appear on TikTok and say "yes, ship it".
- **Scheduling isn't a decision you make — it's a side effect.** Dates are
  auto-assigned at export time by slot-filling (`publisher._schedule_datetimes`).
  You can't say "this idea is for the 24th". There's no view of the week ahead,
  no gaps to spot, no way to plan around an event.

Everything below builds on what's already there: the SQLite `ideas` table
(which already has a `scheduled_for` column), the `jobs` background worker, the
six-stage engine with its QA gate, the Pillow compositor, and the Metricool
export (which — conveniently — already respects a pre-set `scheduled_for`
before falling back to auto slots, `publisher.py:228`).

---

## 2. The new shape: two journeys, one product

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│         CREATE              │        │         SCHEDULE             │
│                             │        │                              │
│  Start: fact / idea / blank │  post  │  Month & week calendar       │
│  → Engine writes the post   │ ─────▶ │  Drag posts between days     │
│  → AI images, per slide     │        │  Unscheduled tray            │
│  → Edit & preview           │        │  "Export week" → Metricool   │
│  → Finish: schedule or draft│ ◀───── │  "+ Create for this day"     │
└─────────────────────────────┘  seed  └──────────────────────────────┘
```

Navigation collapses to **Create · Calendar · Settings** (dashboard stats fold
into the calendar header; Backlog/Build/Review/Trends/Export stop being
destinations and become steps or panels inside the two journeys).

---

## 3. Journey one: Create

One flow, five steps, no dead ends. Every step is skippable-backwards and
autosaves (a post is always an `ideas` row from step 1, so nothing is ever lost
— close the tab, it's a draft).

### 3.1 Step 1 — Start (three doors in)

| Door | What you give it | What happens |
|---|---|---|
| **Facts** | Paste research, stats, studies, myths — or one click pulls from the Trend Scout | A new engine mode turns each fact into 2–3 *angles* (myth-bust, "nobody tells you this", listicle, hot take) and you pick one |
| **Idea** | A rough one-liner, exactly like today's capture | Straight into the engine (current path, kept) |
| **Blank canvas** | Nothing | Pick a proven carousel *mechanic* from a template gallery (myth vs fact, 5 mistakes, before/after, unpopular opinion, "read this before…") and the engine co-writes into that skeleton — or write every slide yourself, fully manual |

**How:** the Facts door is a new pipeline entry — a short "angles" prompt stage
in `pipeline.py` that runs *before* the existing six stages and writes its
output into the existing seed fields (`concept_note`, `core_tension`,
`pain_point`), so the rest of the engine doesn't change. The template gallery
is a static list of mechanic definitions (name, slide-skeleton, example) in a
new `config/mechanics.toml`; choosing one pre-fills the engine's routing so
Stage 2 (format selection) is constrained instead of free. Blank-manual mode
skips the LLM entirely and opens the editor (3.3) with five empty slides.

### 3.2 Step 2 — The engine writes it (and shows its scorecard)

The six-stage build runs as a background job (existing `jobs` worker) with live
progress. When it lands you see the **scorecard**: the QA gate's real numbers
(hook, visual originality, group-chat-share, saveability…) as a visible panel,
not a hidden threshold.

- **3 hooks, not 1.** Slide 1 decides everything on TikTok, so the engine
  returns three hook options; you tap the winner. (One extra array in the JSON
  contract + a picker in the UI — cheap, high leverage.)
- **"Punch it up" per metric.** If `saveability` scored 6, one click re-runs a
  targeted revision prompt: *"rewrite to maximise saveability, keep everything
  else"* — instead of today's blunt full re-request.

**How:** extend the pipeline's JSON schema with `hook_options: [3]`; add a
`revise` job kind that sends the built post + the failing metric back through
one focused prompt. QA thresholds and the re-request loop already exist in
`models.py` / `pipeline.py` — this makes them interactive instead of silent.

### 3.3 Step 3 — Images, for real (the headline fix)

This is where the studio starts *actually creating the photos*:

- **In-journey generation.** The moment you approve the copy, image generation
  starts automatically as a background job — it's a step you watch, not a
  button you have to find. Slides appear one by one as they finish.
- **Per-slide control.** Each slide shows its image, its editable
  `image_prompt`, and two buttons: **Regenerate** (new background, paid) and
  **Re-lay text** (re-run the Pillow overlay only — free, instant — for copy
  tweaks, font/position changes).
- **Variants where it matters.** Slide 1 generates **2–3 background options**
  at high quality (it's the scroll-stopper; ~£0.35 well spent), slides 2–5 one
  each at medium — matching the quality split already in `brand.toml`.
- **Art-direction presets.** A style block (e.g. *Gritty gym flash-photo*,
  *Clean editorial*, *Meme-adjacent*, *Dark cinematic*) chosen once per post
  and appended to every slide's prompt, so the five slides look like one
  carousel and every carousel looks like CHRGD. Presets live in `brand.toml`
  as named prompt suffixes + a consistency clause ("same lighting, same
  location, same colour grade across all images").
- **True-to-TikTok preview.** A phone-frame preview with the safe zones drawn,
  swipeable through the five slides — what you approve is what ships.

**How:**
1. Split `render_carousel` into per-slide units: a `render_slide` job kind so
   slides stream in and one bad generation doesn't restart five. Variants are
   stored as `output/<id>/slide_1_a.jpg`, `_b`, `_c` with the pick recorded in
   `asset_paths_json`.
2. New endpoints: `POST /api/ideas/{id}/slides/{n}/regenerate`,
   `POST /api/ideas/{id}/slides/{n}/recompose` (Pillow-only, no spend),
   `POST /api/ideas/{id}/slides/{n}/pick-variant`.
3. **First task of the whole project: a live paid-path test.** One real
   generation with your key to confirm the model id, response shape, and real
   per-image cost — then update the `_IMAGE_COST` table from actual billing so
   the spend guard is honest. Everything else in this step builds on a path
   we've *proven*, which is the root cause of "it isn't creating the photos"
   today.
4. Prompt upgrade: today's `image_prompt` comes raw from the LLM. Wrap it in a
   composed template — `[style preset] + [slide prompt] + [consistency clause]
   + [negative: no text, no words, no letters]` — since the text is always
   overlaid in code, backgrounds must never contain model-rendered text.

### 3.4 Step 4 — Edit & approve

The existing Review screen's editor (hook, per-slide copy, caption, hashtags)
moves inside the journey as the editing layer of step 3's preview. Copy edits
trigger the free re-compose, not a paid regen. Approve = status `done`.

### 3.5 Step 5 — Finish: schedule it or bank it

The last screen is a **mini-calendar** (next 4 weeks, dots showing what's
already booked):

- **Pick a day/time** → sets `scheduled_for`, post appears on the Calendar.
- **Save as ready** → goes to the calendar's *unscheduled tray*.
- Suggested slot shown by default (first free slot from the existing
  `ScheduleCfg` times), so one tap accepts the sensible default.

**How:** `POST /api/ideas/{id}/schedule {datetime | null}`. The column and the
export logic already exist — this is purely giving the human the pen.

---

## 4. Journey two: Schedule (the calendar)

A proper content calendar at `/calendar`, month and week views:

- **Posts as cards on days** — slide-1 thumbnail, hook, status colour
  (draft / ready / scheduled / exported). Click a card → opens it in Create at
  whatever step it's at.
- **Drag and drop** between days to reschedule; drop onto a day prompts for a
  time slot (defaults from `ScheduleCfg.times`).
- **Unscheduled tray** — a side rail of `done`-but-undated posts you drag onto
  days. Your bank of evergreen content becomes visible ammunition.
- **"+ Create for this day"** on every day cell — starts the Create journey
  with `scheduled_for` pre-filled. *This is the link you asked for: idea for
  two weeks out → click the day → create it → it's already booked.*
- **Decay warnings.** Ideas with `decay_speed = days` that sit unscheduled
  past their shelf life get flagged on the tray ("going stale — ship or void").
- **Export from the calendar.** Select a week → "Export week" runs the
  existing Metricool CSV + `ready/` export for exactly those posts, then marks
  the cards exported. The Export screen stops being a place and becomes an
  action.
- **Gap awareness.** The header shows "3 posts scheduled next week, 4 slots
  empty" — the calendar tells you when to go create.

**How:**
- `GET /api/calendar?from=&to=` returning ideas grouped by day (a date-range
  query over `scheduled_for` plus the undated `done` rows for the tray — the
  schema needs nothing new).
- Reschedule = the same `POST /api/ideas/{id}/schedule` from 3.5.
- Front end stays in the current stack (Jinja templates + vanilla JS —
  no build step, same as the job-polling UI already shipped). Month grid is a
  CSS grid; drag-drop via the native HTML5 API (~100 lines, no library).
- Export filter: extend `MetricoolPublisher.export` with an optional date
  range; auto slot-fill remains only as the fallback for undated posts, so
  nothing existing breaks.

---

## 5. Making it the *best* content, not just finished content

Threaded through both journeys rather than bolted on:

1. **Hook variants + slide-1 image variants** (3.2, 3.3) — the two decisions
   that determine reach get options, everything else gets defaults.
2. **Mechanic templates from proven formats** (3.1) — blank canvas never means
   blank strategy; every route in starts from a format that already works on
   TikTok.
3. **Visible QA scorecard + targeted "punch it up"** (3.2) — the brutal-QA
   stage becomes a tool you wield, not a gate that silently bounces posts.
4. **Style presets + consistency clause** (3.3) — carousels stop looking like
   five unrelated AI images; the brand becomes recognisable in-feed.
5. **Trend Scout feeds the Facts door** (3.1) — topical hooks arrive
   pre-ranked with decay speeds, and the calendar's decay warnings make sure
   fast-decay content actually ships fast.
6. **Later — the learning loop:** paste TikTok analytics (views/saves/shares
   per post) back in; the engine's `learning_tag` field already exists, so
   winning mechanics/hooks/styles can start informing future builds. Out of
   scope for this upgrade, but every schema choice above keeps the door open.

---

## 6. Build plan

Sizing as in ROADMAP.md: **S** ≈ half a session · **M** ≈ one session · **L** ≈ multiple.

| # | Phase | Ships | Size |
|---|---|---|---|
| 0 | **Prove the image path** — live paid test, fix model id/cost table, prompt template + negative-text clause | Real photos, verified cost guard | **S** |
| 1 | **Images in-journey** — per-slide jobs, regenerate / free re-compose / slide-1 variants, style presets, phone preview | The headline fix: creation produces finished visuals | **L** |
| 2 | **Create journey shell** — stepper UI, three doors (facts / idea / blank), mechanics gallery, hook picker, scorecard + punch-up, finish screen with mini-calendar | One flow from nothing → scheduled post | **L** |
| 3 | **Calendar** — month/week views, drag-drop, tray, decay flags, "+ Create for this day", export-week | The planning journey + the link between the two | **L** |
| 4 | **Navigation collapse** — Create · Calendar · Settings; old screens become panels/actions; dashboard folds into calendar header | The product feels like two journeys | **M** |

Order matters: **0 → 1** first because real images are the biggest gap between
"posts exist" and "posts perform", and everything in the Create journey
assumes they work. **2 → 3** can swap if you'd rather see the calendar sooner.
No schema migration beyond additive columns/JSON fields; every phase leaves
`main` shippable and the CLI (`chrgd run` for cron) untouched.

### What I need from you
- Your OpenAI key on the box for the Phase 0 live test (one carousel, ~£0.35).
- A taste-call on the style presets: 3–4 reference TikTok accounts whose
  *visual* feel you want CHRGD to sit alongside.
- Your real posting cadence (posts/day, times) so the calendar's default slots
  and gap warnings match reality.
