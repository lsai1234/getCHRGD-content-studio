# BUILD PLAN — the Show layer and the five shows

The engineering plan for `THEMES_PLAN.md` (decisions D1–D14) and
`MULTIVERSE_ROSTER.md`. Written against real files and functions.

**Sizing**, matching `ROADMAP.md`: **S** ≈ half a session · **M** ≈ one
session · **L** ≈ multiple sessions.

**The rule for the whole plan: everything is additive.** An idea with no show
on its route behaves exactly as it does today, through the same code path. That
means every phase below can ship, be tested against a real post, and be left
alone if you hate it — nothing is a one-way door until Phase 3.

---

## Phase 0 — the Show layer · **M** — ✅ **BUILT**

Shipped: `chrgd/shows.py`, five `config/shows/*.toml`, `config/gate_profiles.toml`,
per-show presets in `brand.toml`, route plumbing through `/api/create/start`,
the show brief in `pipeline.build_user_message`, the look pack + on-image show
name in `images.compose_design_prompt`, gate profiles in `conceptgate.py`,
show-scoped concept sketching and freshness memory in `concepts.py`, `/api/shows`,
and the five show tiles on `/create` with the old six doors behind an
"off-format" escape. 41 new tests in `tests/test_shows.py`; 443 pass.

### One correction to §0.4, found while building it

The plan said furniture toggles were "the single change that lets the Multiverse
stop looking like a CHRGD ad". That was wrong about where the look actually
comes from. On a real render the engine runs `ai_design`, where the image model
designs the WHOLE slide and **no brand frame is drawn in code at all** —
`images.render_slide` returns the model's output untouched, and
`_DESIGN_TEXT_RULES` explicitly forbids logos, labels and watermarks. So
`brand.toml [identity]` only affects the dry-run preview and the legacy overlay
path.

What actually carries a show's look on a paid render is `style_preset` +
`house_style` + the look block, and those are what the implementation leans on.
Two consequences worth knowing:

* **The show name on-image (D10) had to be granted explicitly.** The text rules
  forbid extra labels outright, so a title card is only drawn because
  `compose_design_prompt` now adds it as approved copy — on slide 1 only.
* **Furniture toggles are still wired** (and the Multiverse still runs
  handle-only) because they're correct for the preview path and for any future
  return to overlay rendering — but they are not doing the heavy lifting.

The foundation. No new shows yet — this is the seam that lets the other four
phases be mostly configuration. Build it first and everything after is cheap;
skip it and you write the same five hacks five times.

### 0.1 The Show definition · S

**New: `config/shows/<key>.toml`** — one file per show:

```toml
[show]
key = "amp"
label = "AMP"
title_card = "AMP"          # the on-screen show name (D10)
tagline = "One tip. One charge cycle."
weekday = "wed"
slides_min = 4
slides_max = 6

[spine]                      # the slide roles, in order — a floor, not a cage
roles = ["state", "pull", "the_tip", "working", "payoff"]
briefs = [ "...", "...", "...", "...", "..." ]
flex = 1                     # the engine may add/drop this many slides

[voice]
block = """..."""            # injected into the write call
banned = ["hot take framing", "rage bait", "fake urgency"]

[look]
style_preset = "amp_vector"  # keys into brand.toml [styles.*]
palette = "..."
type_style = "..."
motif = "..."

[look.furniture]             # D3 — overrides brand.toml [identity] per show
wordmark = true
scrim = false
counter = true
handle = true
footer_bar = true
title_card = true

[cast]
mode = "amp"                 # none | amp | roster
[gate]
profile = "comedy_useful"    # which rubric conceptgate runs
[engagement]
default_play = "share"
```

**New: `chrgd/shows.py`** — a `Show` pydantic model plus `load_shows()` /
`get_show(key)`, mirroring `chrgd/mechanics.py`'s `load_mechanics()` /
`get_mechanic()` exactly. Same shape, same caching, same "unknown key returns
None" contract. ~120 lines, no cleverness.

### 0.2 Route plumbing · S

- `route_json["show"] = "<key>"`, set in `webapp.api_create_start`
  (`chrgd/webapp.py:873`) right where `route["mechanic_lock"]` is set today
  (`:856`). Same pattern, same place.
- `pipeline.creation_prefs(idea)` (`:554`) gains `show` alongside the existing
  prefs, so every downstream consumer reads it the same way it reads length
  and mechanic.
- A `shows.show_for_idea(idea) -> Show | None` helper mirroring
  `images.style_for_idea` (`chrgd/images.py:583`), so no module re-parses
  `route_json` by hand.

### 0.3 The write call learns the show · S

`chrgd/pipeline.py`:
- `load_system_prompt()` (`:325`) unchanged — the base engine prompt stays the
  brand's foundation.
- `build_user_message()` (`:350`) gains a **show block** appended after
  `_seed_context()` (`:417`): the show's voice block, its spine as an explicit
  slide-by-slide brief, its banned moves, its slide-count range and its default
  engagement play.
- The spine replaces the engine's free choice of `role` per slide. It stays a
  *floor* (D-level decision in `THEMES_PLAN.md` §4 Q2): `[spine].flex` lets the
  write add or drop a slide when the idea genuinely wants it.

**Why here and not a new prompt file per show:** the JSON contract, the QA
self-scoring and the `Post` model are the same for all five. Only the brief
changes. Five prompt files would drift apart within a month.

### 0.4 The look pack reaches the renderer · M

`chrgd/images.py`:
- `compose_design_prompt()` (`:382`) already takes `design_system` and
  `house_style`; it gains the show's look pack, applied **above** the brand
  profile's house style so a show can genuinely override the account look.
- `_draw_brand_furniture()` (`:661`) and `compose_slide()` (`:721`) read
  furniture toggles from the show, falling back to `brand.toml [identity]`
  when there's no show. **This is the single change that lets the Multiverse
  stop looking like a CHRGD ad** (D3) while the other four keep the frame.
- `style_for_idea()` (`:583`) prefers the show's `style_preset`.
- New brand.toml `[styles.*]` entries per show (`amp_vector`, `comic`,
  `clean_editorial`, `session_reference`, `native_rough`).
- **Title card** (D10): a small show-name treatment drawn in
  `_draw_brand_furniture` when `look.furniture.title_card` is on.

### 0.5 Gate profiles · M

`chrgd/conceptgate.py` currently runs one rubric — rivals → tournament → score
→ glance — tuned to "is this a thumb-stopping hot take". That rubric is the
reason a claims explainer comes out shouty.

- Extract the rubric text in `_concept_payload()` (`:639`), `invent_rivals()`
  (`:297`), `_run_tournament()` (`:761`) and `_run_glance_test()` (`:818`)
  into a **profile** object: `config/gate_profiles.toml`.
- Profiles: `hot_take` (today's, the default and unchanged),
  `comedy_useful` (Amp — is it funny, and can you name the one thing the
  viewer takes away), `claims` (Straight Up — see 2.1),
  `useful_legible` (The Session — could a beginner follow this unaided),
  `continuity_likeness` (Multiverse — see 3.4).
- `gate_slide_one()` (`:875`) selects the profile from the show, defaulting to
  `hot_take`. **Every existing behaviour is preserved as the default**, so no
  current post changes.
- `GATE_STAGES` (`:71`) becomes per-profile so the create screen's live
  checklist shows the checks that actually ran.

### 0.6 The concept engine gets show-aware · S

`chrgd/concepts.py`:
- `sketch_concepts()` (`:503`) gains `show=`: five ideas **inside one show's
  format** rather than five generic ones. `SKETCH_SYSTEM` keeps its spread
  mechanics and swaps its format instructions for the show's.
- `_ensure_amp()` (`:487`) — the forced Amp slot in every set — is **removed**
  once Amp is its own show. It exists today to smuggle Amp into a generic set;
  with a show layer it's redundant and it costs a slot.
- `_already_pitched()` (`:271`) becomes per-show, so freshness memory doesn't
  bleed between formats.
- `cached_concepts()` (`:696`) keys on show.

> **This is the highest value-per-line change in the plan.** Everything else
> improves how a post is *made*; this improves what gets *proposed*, which is
> upstream of everything.

### 0.7 The create screen · M

`chrgd/templates/create.html` (2,623 lines — the biggest single file here).

- Step 1 becomes **five show tiles** plus a small "off-format" escape that
  drops into today's six doors, unchanged. The existing doors keep working —
  they just stop being the front page.
- Each show gets its own second screen. These are new markup on the existing
  quiz-style `.scr` section pattern, so no new framework:
  Amp → the situation + his state · Multiverse → next episode · Straight Up →
  ingredient or question · The Session → the variant matrix · Live Wire →
  today's concepts, retargeted.
- The existing warm-every-lane-on-open behaviour (`:988`) becomes warm-the-
  show's-lane.

### 0.8 Tests · S

`tests/test_shows.py` (new) plus additions to `tests/test_journeys.py`,
`tests/test_conceptgate.py`, `tests/test_images.py`. The critical one is the
**null case**: an idea with no show produces byte-identical output to today.
All offline, injectable clients as everywhere else in this repo.

---

## Phase 1 — AMP + LIVE WIRE · **M** (D4) — ✅ **BUILT**

Shipped: Amp's free state system + situation bank + situation-led brief + his
own create screen; Live Wire's interest territories + territory-aware scout +
its own create screen. 31 new tests (`tests/test_phase1_shows.py`); 474 pass.

Two things found while building:

* **An unknown stored `amp_state` dropped both spines.** The first cut checked
  `route.get("amp_state")` for truthiness, so a typo'd state gave no arc *and*
  no valid state — leaving an Amp slide with no locked character prefix, i.e.
  an off-model mascot on a paid image. It now validates through
  `state_for_route` and falls back to the arc.
* **The Amp brief only fired inside the mechanic-lock branch**, which a post
  started from the AMP show tile never enters — so the show would have written
  a normal carousel that merely looked like Amp once rendered. Hoisted out.

The two shows that were ~80% built. This phase proves the Show layer against
real posts before any new engine gets written.

### 1.1 AMP · S–M

`chrgd/character.py` is in good shape; three changes:

- **`charge_arc()` (`:108`) demotes from mandatory to one spine.** It currently
  forces a monotonically rising charge across slides. Under D5 Amp's state is
  free — deflated one week, beaming the next — so `charges_for_route()`
  (`:217`) returns the arc only when the chosen spine asks for it.
- **An expression/palette system.** `CHARGE_STATES` generalises into
  `AMP_STATES` (drained · flat · wired · beaming · smug · knackered-but-happy),
  each with body colour, posture and mood, driven per slide by the story
  instead of by a charge percentage. `character_block()` (`:137`) is otherwise
  untouched — the locked prefix is what keeps him on-model and it does not
  change.
- **The situation bank** — a stored, tickable list (4am airport, the 3pm slump,
  a broken lift, January in a packed gym, a leaking shaker) with coverage
  tracking that *suggests and never enforces* (D12), reusing the
  `_already_pitched()` pattern.
- **Tip-led** (D5): the `comedy_useful` gate profile fails any Amp post where
  the takeaway can't be named in one line.
- `config/mechanics.toml`'s `amp_charge_cycle` stays for backwards
  compatibility; `is_amp_route()` (`:199`) also matches `route["show"]=="amp"`.

### 1.2 LIVE WIRE · M

The real work is retargeting the scout (D9). `chrgd/trends.py`
`scout_discover()` (`:706`) and `scout_trends()` (`:216`) currently scan "UK
topical", which returns what everyone is already posting.

- **New `config/territories.toml`** — named, weighted interest territories
  replacing the country-wide scan: gym & fitness news (heavy), the science
  (heavy), reality TV, football, holidays & summer (seasonal), money & going
  out.
- `scout_discover()` scans per territory and merges, weighted. `_fuel()` in
  `concepts.py` (`:291`) is unchanged — the leap is fine, it just gets better
  raw material.
- **Overflow behaviour** (D9): Live Wire can bump a scheduled show; the bumped
  show slips a day rather than being skipped. Calendar work, `chrgd/webapp.py`
  + `templates/calendar.html`.
- The `moments` / `trending` / `evergreen` / `ragebait` lanes stay exactly as
  they are behind the off-format escape.

### 1.3 The week · S

Show-aware calendar slots and a "what's due this week" strip. Hard-assign with
an override (§2 Q3 default).

---

## Phase 2 — STRAIGHT UP + THE SESSION · **M** — ✅ **BUILT**

Shipped: `config/ingredients.toml` + `chrgd/ingredients.py` (13 entries, each
with an empty `our_product` hook per D7), `chrgd/claims.py` (the lint + the
judge), `config/session_variants.toml` + `chrgd/sessions.py` (the matrix and the
staleness nudge), both briefs wired into the build, and both shows' own create
screens. 48 new tests (`tests/test_phase2_shows.py`); 522 pass.

**The claims gate is the first check here allowed to STOP a post.** Everything
else in this codebase flags and lets the render proceed; a compliance check
shouldn't. A flagged STRAIGHT UP post is marked `review` with the reason
attached rather than shipped — a false positive costs an editor thirty seconds,
a false negative puts a health claim on a public account. It's two layers:

* **the lint** — deterministic patterns, no key, no network, runs on *every*
  post everywhere (advisory outside STRAIGHT UP). Deliberately narrow: only
  phrasings that are wrong regardless of context. Over-flagging trains the
  editor to ignore the gate, which is worse than not having one, so "£45 for
  flavoured maltodextrin" and "the evidence is thinner than the marketing
  suggests" must pass — and there are tests asserting they do.
* **the judge** — a cheap LLM pass for implied promises a regex can't see.
  Only runs on a claims-gated show, deduped against the lint, and never fatal.

Mostly config plus two gates and one picker. No new architecture.

### 2.1 STRAIGHT UP · M

- **New `config/ingredients.toml`** — the show's backlog: creatine, caffeine,
  beta-alanine, EAAs, electrolytes, ashwagandha, collagen, citrulline… Each
  entry carries what it is, what the evidence supports, the common myth, and
  **an empty `our_product` field** (D7) so wiring your range in later is
  filling a field, not a re-architecture.
- **The `claims` gate profile** — a distinct pass, not a score inside the
  write's self-audit. It fails: outcome promises, unsourced numbers,
  medical/therapeutic framing, and "studies show" without a study. It permits
  industry opinion (pricing, labelling, marketing) per D2.
- Front screen: pick an ingredient or paste a question. The existing `facts`
  door (`webapp.py:905`) is already 90% of this journey.

### 2.2 THE SESSION · M

- **The variant matrix** (D11) — goal · when · how · where · level ·
  supplement tie-in, as a picker on the show's second screen. Goal-framed, no
  gender labels; **no-equipment is a standing axis**, not a fallback.
- **Coverage nudges, never enforces** (D12): "no no-equipment session in four
  weeks" as a suggestion the operator can ignore. Two of the same type in a
  week is allowed by design.
- **The `useful_legible` gate profile** (D8, D14): scores whether a beginner
  could follow it unaided, and bans %1RM, RPE scales and jargon assuming
  training history. Named exercises, sets and reps, effort in plain words.
- The `Slide.body` field (`chrgd/models.py:62`) already exists for exactly the
  dense readable block a session slide needs — no model change required.

---

## Phase 3 — THE MULTIVERSE · **L**

The only phase with real new engine. Built last, deliberately: by then the
Show layer is proven and you'll have posted enough to know what works.

### 3.1 The roster · M
**New `config/roster.toml`** from `MULTIVERSE_ROSTER.md` — twelve characters,
each with the locked comic visual prompt, the joke-generating trait, the gym
role and the **safety class** (`meme_character` / `public_figure` /
`fictional_ip`). **New `chrgd/roster.py`**: load, cast 2–4 per episode, and
emit each character's locked prefix, mirroring `character.py`'s proven pattern.

### 3.2 Canon, authored in the studio · M
**New `chrgd/series.py`** + a `series` / `episode` store (`chrgd/db.py`
`Store`). Each episode writes what changed — who won, who lost, who now hates
whom, what's unresolved — and the next episode reads it. Plus the **cold-open
recap generator**: slide 1 always lands for someone who's never seen it (D6).

**Operator-authored (D16).** The canon is not engine-only: a studio screen
lets you write and edit storylines and history directly, and a **hard reset**
wipes the canon and starts the world clean. The engine proposes the next
episode from the log; you own what the log says.

### 3.3 The episode pipeline · M
`STORY_ENGINE_PLAN.md`'s stages, which already exist as a design: arc → beat
sheet → copy. New job kinds on the fast worker lane (`chrgd/worker.py`
`_HANDLERS`, `:612`): `series_bible`, `episode`.

### 3.4 The two gates · M
The `continuity_likeness` profile:
- **Continuity** — names, traits and established canon match the roster and the
  last episode.
- **Grip** — every slide but the last ends on an open loop.
- **Likeness** (`THEMES_PLAN.md` Show 2) — enforced in code, not remembered
  weekly: caricature never photoreal; no real person using or endorsing a
  product; no fabricated quotes or news framing; punch at status and situation
  only; cast only from the approved roster. `public_figure` and `fictional_ip`
  entries get the full check, `meme_character` entries skip it.

### 3.5 The comic look · S
`brand.toml [styles.comic]` + the Multiverse's furniture set to handle-only
(D3). Bold ink, halftone, panel captions, speech bubbles.

### 3.6 The vote · S — optional
Downgraded by D16: with canon authored in the studio, the serial no longer
depends on an audience vote to know what happens next. Cliffhangers carry the
grip; the vote becomes a comment mechanic you can switch on per season rather
than a structural requirement.

---

## Phase 4 — measurement, then video · **M**

- **Per-show analytics** (`chrgd/analytics.py`, `chrgd/learning.py`): tag
  performance by show against each show's named KPI. After eight weeks you
  know which of the five to double down on and which to kill. Without this the
  whole plan is a guess that never resolves.
- **Amp video pilot** (D5): M6 is built and flagged off (`chrgd/video.py`,
  `CHRGD_VIDEO_ENABLED`). Flat vector animates far better than photoreal, so
  Amp is the right first test when you have a Higgsfield key and ffmpeg on the
  host.

---

## What this plan deliberately does not do

- **No rewrite of the write call.** `content_engine_prompt.md`, the JSON
  contract and the `Post` model serve all five shows. Only the brief changes.
- **No new framework on the front end.** The create screen's quiz-style
  sections are extended, not replaced.
- **No removal of the existing doors.** They move behind an "off-format"
  escape and keep working.
- **No change to the render pipeline's shape.** Slide-1 concept gate before
  spend, scroll test after — unchanged, just with per-show rubrics.

## The risks worth naming

1. **The Multiverse is the one that can genuinely fail.** A serial dies if
   episode 2 doesn't hold episode 1's audience, and no engine fixes that.
   `STORY_ENGINE_PLAN.md`'s advice still stands: write one season's opener by
   hand and post it before building 3.1–3.6.
2. **Five shows a week is five times the concept load.** The show-aware sketch
   (0.6) is what makes that survivable; if it under-delivers, the whole cadence
   is at risk, so it's worth testing hard in Phase 0.
3. **Per-show looks can fragment the grid.** D3 keeps four of five on shared
   furniture precisely for this. Worth a look at the profile grid after two
   weeks.

## Suggested order

**0 → 1 → 2 → 3 → 4**, and stop after any phase. Phase 0 + 1 is the one that
changes how the studio feels day to day; everything after it is content.
