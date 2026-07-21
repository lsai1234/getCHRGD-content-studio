# JOURNEY_REVIEW — The Creation-Entry Journey (UX · Performance · Angle Strategy)

**Scope:** app open → angle/topic chosen → generation kicked off. Mobile-first, sole operator, carousels only. Every claim cites the file/function it comes from. Fixes live in `JOURNEY_CHANGE_PLAN.md`.

## Executive summary

The single biggest thing making this journey feel slow is not one fat request — it's the **single-threaded FIFO job worker turning every discovery scan into a serialized 30–90s blocking wait** (`worker.py:29-93`, FIFO claim in `db.py:480`). Each radar lane is one giant web-search completion (6 stories × 2–3 fully-written angles before a single pixel renders, `trends.py:scout_discover`), lanes queue *behind each other* when you tab between them, and a stale `meta_scan` silently jumps into the same queue ahead of your next action (`worker.py:211-213`). Compounding it: scan failures are misreported as "came back empty" because the status endpoint only ever reads COMPLETED jobs (`webapp.py:api_moments`), and one error path renders a retry link that literally does nothing (`create.html:1038`). The single biggest thing capping output quality is that **the creative work of the discovery lanes is done by the cheapest model against selection criteria engineered for consensus and mainstream** — ragebait's SPLIT TEST (`trends.py:501-507`) explicitly optimises for balanced both-sides debates (that's a debate club, not rage bait), and trending ranks by "how live" (`trends.py:478-479`), which selects exactly the saturated trends everyone has already covered — all written by `gpt-4o-mini` (`config.py:42`, `trends.py:125-137`).

---

## The journey as it actually runs (map)

```
Open app → "/" 302 → /calendar (webapp.py:286-291)     ← home is the planner, not creation
  └ tap "Create" (top nav, base.html:158)
/create → scr-source: 8 equal option cards (create.html:272-326)
  ├ moments/trending/evergreen/ragebait → scr-radar, 4 lane tabs (create.html:333-360)
  │    loadLane(): GET /api/moments?kind=<lane>  ← cached last scan (instant)
  │    stale/empty → refreshLane(): POST /api/jobs/moments → 30-90s scan job → poll 2.5s
  │    tap story → tap angle → "See the takes" → POST /api/moments/{job}/use
  ├ idea  → textarea → Enter → POST /api/create/start (takes)
  ├ facts → textarea → POST /api/create/start → angles job → poll → pick
  ├ format → mechanic gallery → POST /api/create/start (blank)
  └ manual → straight to editor
  → takes job (~20s+, poll 2s) → tap take → "Write this one" → build job = KICKOFF
```

Tap count, happy path via radar: **6 taps + 2 long waits** (source card → story → angle → wait → take → write). Via own idea: 5 taps + 1 wait.

---

## Pillar A — UX & mobile-first

### A1. The source screen is a wall of eight equal doors — **HIGH**
`create.html:272-326`: moments, trending, evergreen, ragebait, idea, facts, format, manual — eight cards of identical visual weight answering "What are we making today?". Four of them (`moments/trending/evergreen/ragebait`) route to the *same* radar screen with lane tabs (`routeToDoor`, `create.html:742-749`) — one door pretending to be four. For a sole operator opening this daily on a phone, that's a decision wall where there should be a default. Nothing remembers what you used last, nothing suggests today's best starting point, and the screen requires reading ~90 words of card copy before the first tap. **Fix:** collapse to three groups with a hero default (see plan §3.1).

### A2. Real information hidden in hover-only `title` tooltips — **HIGH (format door), MEDIUM elsewhere**
Mobile has no hover, and the journey hides load-bearing content in `title=` attributes:
- The format gallery's *actual skeletons* — the thing you're choosing between — exist only as `title="{{ m.skeleton|join(' → ') }}"` (`create.html:400`). On a phone the picker shows a label and one description line; the five-beat structure you're committing to is invisible.
- Every "🎯 See the takes / ⚡ Just build it" pair explains the difference only via `title` (`create.html:355-356, 372-374, 971-972`).
- The length chips' slide-count meanings are `title`-only (`create.html:2016-2023`).

### A3. Sub-44px touch targets on the most-used controls — **MEDIUM**
The `.chip` class (padding 7px 14px, 13px font → ≈33px tall, `base... create.html:71-75`) is used for exactly the controls a thumb hits most: the four radar lane tabs, ↻ Rescan, the length picker, feedback chips. `.angle-actions button` (7px 13px, 12.5px → ≈32px, `create.html:103-104`) are the actual "go" buttons on every angle. The back button is a 20px glyph with 2px vertical padding (`create.html:16-17`). Apple/Google minimum is 44/48px. The big `.opt` cards are fine (≥66px).

### A4. Top-nav-only navigation, thumb-hostile on a phone — **MEDIUM**
All navigation is a sticky *top* bar (`base.html:44-69`); at ≤600px it wraps to two rows of 13px links eating viewport. There is no bottom tab bar and no floating primary action. The journey's primary CTAs (`.cta-row`) sit at the end of content, not sticky — fine on the short entry screens, but the pattern means the "go" button is wherever the content ends, not where the thumb is.

### A5. Missing / broken states inventory — **HIGH (as a set)**
- **Dead retry link:** `pollDig` error renders `<a href="#" onclick="return false">try again</a>` — a retry that does nothing (`create.html:1038`).
- **Silent network failure:** `loadLane()` does `if (!r.ok) return;` — the skeletons shimmer forever with no error, no retry (`create.html:886-887`).
- **Errors misreported as empty:** a scan job that ERRORs is invisible to `GET /api/moments` (it only queries `status = 'COMPLETED'`, `webapp.py:api_moments`), so `pollLane` falls through to "scan came back empty — try rescan" (`create.html:917`) when the truth is "the API call failed: <reason>". The queue page knows; the journey lies.
- **`alert()` as the error/validation UI:** `post()` and `fail()` use blocking system alerts (`create.html:1995-2008`), including for simple validation ("Give the idea a line of text first", `create.html:1086`). On mobile this is the single most un-professional-feeling element in the journey.
- **No in-flight guard on Rescan:** double-tapping ↻ re-posts (server dedupes, `worker.py:enqueue_discover`, but the UI gives no pressed/disabled feedback).

### A6. Polish details — **LOW**
- `--faint` (#6a7480) placeholder/small text on panel backgrounds ≈ 2.9:1 contrast — fails WCAG AA for the 10.5–12px sizes it's used at (`base.html:14`, eyebrow/tags at `create.html:32, 120`).
- Lane switch always flashes skeletons even when the cached result returns instantly (`loadLane` resets to skeletons before fetching, `create.html:883-884`).
- The Quick-hit length chip still promises "roughly 1–2 slides" (`create.html:2019`) — the engine pref was changed to 3–4 (`pipeline.py:LENGTH_PREFS`). UI/engine drift.
- The facts door links out to the *legacy* `/trends` page in a new tab (`create.html:385-386`, `trends.html`) — a second, older trend surface with different UI splitting the same job across two screens.

**What's genuinely good (keep):** the quiz-style one-screen-at-a-time structure with progress rail (`create.html:5-30, 686-715`), skeleton shimmer placeholders (`create.html:126-129`), cached-lane-stays-visible-while-background-rescanning (`loadLane` passes `showSkeleton=false` when content exists, `create.html:892-893`), elapsed-time + expectation copy on takes/build/develop waits, and resume-by-URL (`create.html:2026-2050`).

---

## Pillar B — Performance & data-fetching

### B1. The single-worker FIFO queue serializes every long job — **CRITICAL**
All async work — scans, takes, builds, renders, meta research — funnels through **one** in-process worker thread processing **one job at a time** (`worker.py:29-93`; FIFO claim `db.py:claim_next_job:480`; single-consumer design stated in the module docstring, `worker.py:6-9`). Consequences you feel directly:
- Open Trending while Moments is scanning → the Trending scan *queues behind it* (per-lane dedupe only, `worker.py:enqueue_discover:497-508`); worst case on first use, 4 lanes × 30–90s each, strictly sequential.
- `_handle_takes` silently enqueues a `meta_scan` when the meta is stale (`worker.py:211-213`) — a ~60s research job now sits in the FIFO **ahead of your next tap's job**. This is the "big all-in-one background refresh" you suspected: not one monolithic request, but background research jobs cutting in line on a single-lane road.
- A queued render or build behind any scan waits the full scan out.

### B2. Each scan is a monolith: everything generated before anything shows — **HIGH**
One lane scan = one web-search completion that must research *and* fully write 6 stories × 2–3 angles (hooks + concept notes) before returning (`trends.py:scout_discover:611-627`, `_DISCOVER_ASKS:566-590` "Find up to {count}… each with 2-3 ready angles"). Nothing streams; the UI gets 100% or nothing. But the user's actual need is two-stage: **headlines fast, angles on demand for the one story they tap** — a pattern the codebase already proves with `moment_detail` dig (`trends.py:680-698`, `create.html:digMoment`). The fetch is scoped per-lane (good — no aggregate refresh exists), but each lane's payload is ~5× what the first paint needs.

### B3. Freshness is client-side, on-open only — the first open of the day always pays — **MEDIUM**
A lane rescan triggers only when *you open the lane* and the cache is older than `LANE_MAX_AGE_H` (moments 12h / trending 24h / evergreen 7d / ragebait 3d — `create.html:867-893`). There is no server-side warm: no scheduled morning scan, no warm-on-login. So the every-morning experience is: open Moments → stale → wait the full scan. (The weekly systemd timer runs the build chain, not the radar; `meta_scan` has auto-refresh but the four lanes don't.)

### B4. Error truth never reaches the journey — **HIGH** (same root as A5)
`GET /api/moments` selects only `status='COMPLETED'` jobs and returns `scanning` from QUEUED/PROCESSING (`webapp.py:api_moments`). A job in ERROR is invisible → the UI's only vocabulary is "empty". The fix is one column in the response (`last_error`) + three lines of JS.

### B5. Polling is chatty but acceptable — **LOW**
`pollLane` refetches the full lane payload every 2.5s; takes/build/develop poll `detail()` every 2s, and each `api_idea_detail` call does up to 80 `Path.exists()` checks via `list_variants` (`webapp.py:api_idea_detail`, `images.py:list_variants`). Fine on wifi, mildly battery/data-hostile on mobile; not the bottleneck. No debouncing issues found; `enqueue_*` helpers dedupe server-side correctly.

### What each action actually fetches (trace)

| Action | Calls | Blocking? | Cached? | Scoped correctly? |
|---|---|---|---|---|
| Open /create | none (template only) | — | — | ✅ |
| Open a radar lane | `GET /api/moments?kind=<lane>` | no (instant) | ✅ last completed scan | ✅ per-lane |
| Stale/empty lane | `POST /api/jobs/moments {kind}` → poll 2.5s | **yes, 30–90s** on empty; background if cached | job result cached | ⚠ per-lane but monolithic (B2) + serialized (B1) |
| ↻ Rescan | same as above | background (cached stays visible) | — | ✅ |
| Dig a moment | `POST /api/moments/{job}/dig` → poll | yes (~20-40s) | job result | ✅ properly scoped two-stage |
| Pick angle / own idea | `POST .../use` or `/api/create/start` → takes job → poll 2s | yes (~20s) | — | ✅ |
| (hidden) stale meta | `meta_scan` job auto-enqueued during takes (`worker.py:211-213`) | not directly — but blocks the FIFO for the *next* action | 7-day cache | ❌ cuts the queue |

---

## Pillar C — Strategic quality of the angle options

Model note that applies to every lane: all four discovery prompts run on `scout_model = gpt-4o-mini` (`config.py:42`, `OpenAITrendClient.__init__`, `trends.py:125-137`). Research-aggregation is fine on a cheap model; **the hooks and claims inside the scan output are shipped creative**, and they're being written by the cheapest model in the building while the build stage gets the premium one. This alone explains a large share of "tame" and "generic".

### C1. Ragebait — engineered for balanced debate, not rage — **CRITICAL (quality)**
The prompt (`trends.py:RAGEBAIT_PROMPT:486-558`) asks for "genuinely divisive debates," and its hard filter is the SPLIT TEST (`trends.py:501-507`): *"can you name BOTH camps, and are both big? … The perfect take makes half the audience type 'finally someone said it' and the other half type an essay."* That is an instruction to find **symmetric, well-worn debate topics** — and the prompt even supplies the tired hunting grounds ("training-split tribalism, cardio vs weights, gym etiquette wars…", `trends.py:495-499`). Symmetric debates are safe, familiar, and tame: everyone has heard cardio-vs-weights; nobody's identity is threatened.

What the mechanic actually requires (and the prompt never asks for):
- **An identity-threatening claim**, not a topic: something that invalidates a choice the viewer has *spent money, effort, or self-image on* — sunk cost ("you've been paying for…"), effort invalidation ("your 2-hour session is…"), status inversion ("that's beginner behaviour"). Compulsion-to-correct comes from threat, not disagreement.
- **Engineered asymmetry with the buyer on the winning side.** The prompt has *no concept of the buyer at all*. Rage should come from an out-group (a rival tribe, a habit, a product category, a myth); the in-group — people who train seriously and buy supplements — should be cheering and sharing. The SPLIT TEST's "both camps big" requirement actively *rejects* this shape.
- **A receipt.** "Genuinely defensible" is asserted (`trends.py:508-522`) but no field forces the defense to exist. Every claim needs the one fact we reply with when the comments kick off.
- The downstream build framing is actually decent — commit, leave one counter-argument unaddressed, invite the other side (`pipeline.py:build_user_message` RAGEBAIT block) — the weakness is upstream: the scout hands it soft material.

### C2. Trending — ranks by biggest, which selects mainstream — **CRITICAL (quality)**
The prompt (`trends.py:TRENDING_PROMPT:399-480`) has genuinely good machinery: the CAROUSEL TEST (`:425`), the FEED TEST (`:436`), spike/wave horizon. But three things guarantee generic output:
1. **The ranking rule selects saturation:** *"Rank by how live AND how carousel-buildable each trend is — native, peaking spikes first"* (`trends.py:478-479`). "Most live" ≡ "most covered." There is no tier filter (emerging / rising / peaked / saturated), no hijack-potential check, and no penalty for "every brand is already on this."
2. **No rejection of the obvious take.** The angle recipe is *"one straight execution of the format in gym terms, one funny/relatable twist, one light product tie-in"* (`trends.py:449-452`) — the "straight execution" IS the generic mainstream take the brief complains about. The mechanic needs the intersection: rising trend × our niche × non-obvious angle, with the obvious take explicitly generated *and discarded* so the model is forced past its first answer.
3. **Single-stage monolith** (see B2) — depth of research and sharpness of angles compete for the same completion budget.

### C3. Moments — sound for its purpose, thin on angle craft — **MEDIUM**
The AWARENESS TEST (`trends.py:259-267`) deliberately selects mass-mainstream moments — correct for this lane (events are the star, and the account's one 20k-view hit was a moment post). But the angle recipe is the same thin trio (advice / funny / tiein, `trends.py:269-277`) with no requirement for the insider-specific detail (the 2am alarm, the exact kick-off time) that made the England post work, and no counter-programming instruction (everyone posts the same moment; ours needs the angle nobody else takes).

### C4. Evergreen — the strongest lane prompt — **LOW**
`trends.py:EVERGREEN_PROMPT:341-394` has a real quality bar ("wait, WHAT?" reaction, true and checkable, on-world, believable brand bridge). Minor gap: no per-fact share-identity requirement ("sending this says ___ about me"), which is the difference between interesting and shared.

### C5. Facts door (angles) — a thin prompt next to its sibling — **MEDIUM**
`ANGLES_CONTRACT` (`pipeline.py:849-876`) asks for "genuinely different takes (myth-bust, 'nobody tells you this', listicle, hot take, story…)" — note it *recommends* "nobody tells you this," a phrase on the engine's own banned-hooks list (`content_engine_prompt.md:64`). Unlike `TAKES_CONTRACT` (`pipeline.py:927`) it requires no target emotion, no share-identity, no precedent anchor. Same journey, half the rigor.

### C6. Format door (mechanics gallery) — templates that contradict the engine — **MEDIUM**
`config/mechanics.toml` skeletons hard-code hooks the engine prompt bans: `five_mistakes` slide 5 is *"The mistake NOBODY talks about"* (`mechanics.toml:27`) and the whole `nobody_tells_you` mechanic opens with *"'Nobody tells you this about <topic>' — conspiratorial hook"* (`mechanics.toml:63-72`) — directly against the banned list *"nobody talks about"* (`content_engine_prompt.md:64`). The engine is told to follow the skeleton (`pipeline.py:build_user_message` FORMAT CONSTRAINT), so the format door reliably produces banned-hook posts. The gallery also carries no psych fields (no emotion, no engagement play) — it's structure without a mechanic.

### C7. Idea door / takes fan-out — well-engineered — **keep**
`TAKES_CONTRACT` (`pipeline.py:927-`) is the model the others should meet: evidence-first precedent, high-arousal emotion check, share-identity completion, banker/unhinged/debate spread, account-history weighting, pillar coverage. The ragebait own-beef path correctly rides the fight framing (`webapp.py:api_create_start` ragebait moment; `create.html:1054-1077`).

---

## Graded findings index

| # | Finding | Pillar | Grade |
|---|---|---|---|
| B1 | Single-worker FIFO serializes scans; meta_scan cuts the queue | Perf | **Critical** |
| C1 | Ragebait prompt optimises symmetric debate; no buyer-side asymmetry, no identity threat, no receipts | Strategy | **Critical** |
| C2 | Trending ranks by biggest/most-live; no tier filter, no obvious-take rejection | Strategy | **Critical** |
| B2 | Monolithic per-lane scan; needs headline-first two-stage | Perf | High |
| B4/A5 | Scan errors invisible → "empty" lie; dead retry link; alert() UI; silent fetch failure | Perf+UX | High |
| A1 | Eight-door source wall, no default, 4 doors = 1 screen | UX | High |
| A2 | Load-bearing info in hover-only `title` (format skeletons especially) | UX | High (format) |
| C0 | All lane creative written by gpt-4o-mini | Strategy | High |
| B3 | No proactive warm; first open of the day always pays the scan | Perf | Medium |
| A3 | Sub-44px chips/angle buttons/back button | UX | Medium |
| A4 | Top-nav only; no thumb-zone primary action | UX | Medium |
| C3 | Moments angle recipe thin (no insider-detail/counter-programming) | Strategy | Medium |
| C5 | Facts-door angles contract lacks takes-level rigor; recommends a banned hook | Strategy | Medium |
| C6 | mechanics.toml skeletons embed banned hooks | Strategy | Medium |
| B5 | Chatty polling, `list_variants` disk churn per poll | Perf | Low |
| A6 | Contrast fails on faint text; skeleton flash on cached lane switch; Quick-hit says 1–2 slides (engine: 3–4); legacy /trends link | UX | Low |
| C4 | Evergreen lacks share-identity per fact | Strategy | Low |

Prescriptions, rewritten prompts (ragebait + trending in full, with example outputs), and diffs: `JOURNEY_CHANGE_PLAN.md`.
