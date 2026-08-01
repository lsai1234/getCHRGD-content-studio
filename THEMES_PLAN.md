# THEMES_PLAN — five shows, five journeys, one engine

**Status: draft v0 — written to be argued with.** Every section ends with
*Questions for you*; my recommended default is marked **→ default** so you can
reply "default" and move on where you don't care.

---

## 0. The diagnosis (why the create journey feels like it's doing too much)

Today `/create` has six doors — today's concepts, explore what's hot, my own
idea, from research, a proven format, fully manual — and **every one of them
funnels into the same place**: `POST /api/create/start` with `mode` set to
`idea` / `facts` / `blank` / `manual`, then one write call against
`content_engine_prompt.md` + the JSON contract in `chrgd/pipeline.py`.

That means the doors differ only in *where the seed came from*. After the seed,
everything is identical:

| Layer | Where it lives | Same for every door today |
|---|---|---|
| The brief / voice | `content_engine_prompt.md`, `brand_bible.md`, `profile_engine_block` | ✅ identical |
| The structure | engine free-picks 1–10 slides + roles; a mechanic skeleton only if you used the format door | ✅ effectively identical |
| The look | `brand.toml` `[colors]` near-black `#0B0B0D`, `[identity]` wordmark + scrim + counter + footer bar, `[styles.*]` all four gym-photography presets | ✅ identical |
| The quality gate | `conceptgate.py` rivals → tournament → score → glance, one rubric | ✅ identical |
| The character | Amp via the `amp_charge_cycle` mechanic only | opt-in, but the *look* is still the dark house look |

So a supplement explainer, a Love-Island parody and a leg-day workout all come
out of the same dark, gritty, flash-photo, thumb-stopping-hot-take machine.
The engine is good at *one* thing and we keep asking it to be five things with
the same prompt and the same palette.

**The fix is not more prompt.** It's a layer the engine currently doesn't have:
a **Show** — a reusable, named creation path that owns its own structure, its
own look, its own rules, its own gate and its own front screen. Then the create
screen stops asking "where's your seed from?" and starts asking **"which show
are we making today?"** — which is also how you actually think about it.

Nothing below throws work away. The concept engine, the trend scout, the
conceptgate tournament, Amp, the mechanics gallery, the render pipeline, the
calendar and the export all stay and get *pointed* by the show instead of
being one-size-fits-all.

---

## 1. The five shows

Working titles — naming questions at the end of each. One a week, so the week
has a shape and the audience learns the appointment.

### Show 1 · **AMP** — the mascot's charge cycle (stories + tips)

*Fun · character-led · the brand's face*

| | |
|---|---|
| **What it is** | Amp gets flattened by something relatable, fights back, ends fully charged. Two sub-modes: **Story** (pure comedy arc, no lesson) and **Tip** (the turnaround *is* the tip — one usable thing, taught through the gag). |
| **Spine** | 1 drained + the drain event · 2 it gets worse · 3 the turn (what he tries) · 4 charging (it's working, with the actual tip if Tip mode) · 5 fully charged + "Stay amped." |
| **Look** | Flat vector, sticker energy, bold outlines, electric cyan `#29C2F2` on the brand near-black. **No photoreal, ever.** Reads as a comic strip, not a gym photo. |
| **Cast** | Amp, locked. `chrgd/character.py` `CANONICAL_PROMPT` + `STYLE_LOCK` + `charge_arc()` already do this. |
| **Wants** | Recognition + a smile. KPI: shares ("this is me on leg day"). |
| **Reuses** | `character.py` (built), `amp_charge_cycle` in `config/mechanics.toml` (built), the Amp slot already in the concept sketch prompt (built). |
| **Needs new** | The Story/Tip split; a **drain-event bank** so he isn't drained by leg day every week; the charge-cycle spine enforced as a real skeleton rather than a suggestion; a *comedy* gate instead of the hot-take gate. |

**The risk I'd flag:** one mechanic, weekly, forever = sameness. The charge
cycle is a strong container but the *drain* has to be genuinely new each time
(4am airport, the 3pm slump, a stag do, a broken lift, January in a full gym,
a shaker leaking in a gym bag). I'd build the drain bank as a stored, tickable
list so you can see what's been used — same idea as the "already pitched"
memory in `concepts.py`.

**Questions for you**
1. **Story vs Tip split** — alternate weekly, or Amp is always a story and the tip is a bonus on the last slide? **→ default: alternate, with the Tip weeks carrying one genuinely useful thing.**
2. Does Amp ever **talk** (speech bubbles) or is it always narration + his face? **→ default: speech bubbles, sparingly — they're funnier.**
3. Is Amp allowed to **cameo** in the other four shows (e.g. a corner sticker on a supplement post), or is he ring-fenced to his own day? **→ default: ring-fenced for now — cameos dilute the appointment.**
4. Do you want **a supporting cast** for Amp (a gym-bro rival, a knackered mate) or is he a solo act? **→ default: solo for the first 8 weeks, then one rival.**
5. Is the Amp day the one that could become **video** first (M6 is built, flagged off) — flat vector animates far better than photoreal? **→ default: yes, Amp is the video pilot when you turn it on.**

---

### Show 2 · **THE VILLA** — gym reality-format parody (serial)

*Fun · colourful · zero brand furniture · the one people follow for*

| | |
|---|---|
| **What it is** | A reality dating-show format transplanted into a UK commercial gym at 6pm. Own cast, own villa. **A serial** — the same people come back next week, and last week's vote is this week's canon. |
| **Spine** | 1 recap-that-also-works-cold · 2 the situation + the threat · 3 the complication · 4 escalation · 5 the turn · 6 cliffhanger + **the vote** |
| **Look** | **Deliberately not the house look.** Sunlit, saturated, holiday-bright — coral/turquoise/hot-yellow, reality-TV lower-third name bars, on-screen captions in the show's own type. No near-black, no scrim, no battery, no Amp. |
| **Cast** | 3–5 named regulars with a locked visual per character (the same locked-prefix trick Amp uses, one per cast member). |
| **Wants** | Comments and returns. KPI: comment volume on the vote + week-2 retention. |
| **Reuses** | `STORY_ENGINE_PLAN.md` is already the full blueprint for this (bible → arc → beat sheet → copy → continuity gate); the locked-character pattern from `character.py`. |
| **Needs new** | Everything in that plan: `chrgd/series.py`, series + episode storage, the continuity gate, the vote mechanic. **This is the only show that needs real new engine, not just config.** |

**Brand safety, decided up front:** parody the *format*, never the property. No
show name, no logo, no real islanders — our own villa, our own cast. Costs
nothing creatively (the comedy is in the format and the characters) and keeps
the account off a rights-holder's radar. Already argued in `STORY_ENGINE_PLAN.md`
and I'd hold that line.

**Questions for you**
1. **Serial or standalone?** Same cast every week with a cliffhanger and a vote (high ceiling, high commitment — if it flops at episode 3 you're stuck), or a new self-contained parody each week that just borrows a format (safer, weaker)? **→ default: serial. It's the only one of the five with a real reason to follow the account.**
2. **One format or a rotating parody slot?** Villa forever, or does this slot rotate — villa / mockumentary office (your Hyrox-boss idea) / talent-show / dating-app? **→ default: Villa runs a full "series" of ~8 episodes, then the slot rotates to the next format. Seasons, not forever.**
3. **Cast** — do you want to write the 3–5 characters yourself (fastest way to find out if it's funny), or should I generate a bible for you to edit? **→ default: I generate a first bible, you rewrite the traits — the trait is the joke engine and it should be yours.**
4. **The vote** — is the audience's choice genuinely canon next week (strongest mechanic, means you can't pre-batch episodes), or a soft "who's your favourite"? **→ default: genuinely canon. It's the whole reason to comment.**
5. Real-ish **photographic** cast (gritty flash photo, feels like a real gym) or **illustrated**? Photographic is funnier and harder to keep consistent. **→ default: photographic, with a locked portrait per character.**
6. Does the brand appear at all — shakers in shot, a logo on a vest — or is it pure entertainment with the handle only? **→ default: props only, never a pitch.**

---

### Show 3 · **STRAIGHT UP** — supplements, facts, actual help

*Serious · clean · trustworthy · the show that sells*

| | |
|---|---|
| **What it is** | The honest answer to a question people genuinely search: what creatine actually does, whether you need EAAs, what "proprietary blend" hides, when protein timing matters and when it doesn't. No hype, no fear. |
| **Spine** | 1 the question, in their words · 2 the short answer (given away immediately — trust beats tease here) · 3 why / the mechanism, plainly · 4 what it means for you (dose, timing, practicalities) · 5 what to ignore / the myth · 6 save-this |
| **Look** | Bright, clean, editorial. Light background or crisp product-on-seamless, generous whitespace, one accent, large legible type. The *opposite* of the gritty night-flash look. Legibility is the aesthetic. |
| **Cast** | None. No Amp, no characters — a mascot undercuts authority. |
| **Wants** | Saves + follows. KPI: saves and profile visits. |
| **Reuses** | The `facts` door (paste research → angles) is already exactly this journey's front end; `myth_fact` / `insider_mechanism` in `config/mechanics.toml`; the `claim_safety` QA score already in the contract. |
| **Needs new** | A **claims gate** with teeth (a separate pass, not one score inside a self-audit); a source/citation field carried through to the slides; an ingredient/product library so the show has a backlog rather than being invented weekly. |

**The thing that decides this show:** on TikTok, a UK supplement brand making
health claims is a real regulatory and platform risk, and "hedged into
uselessness" is the failure mode on the other side. So the gate needs an
explicit, written line — see the question below, it's the one I most need you
to answer.

**Questions for you**
1. **How far do we go on claims?** (a) strict — describe, never promise, no
   health outcomes at all; (b) educational — cite the evidence and say what it
   does and doesn't support; (c) opinionated — take a side on contested stuff
   ("most people don't need BCAAs"). **→ default: (b) with (c) reserved for
   *industry* opinions (pricing, labelling, marketing), never physiology.**
2. Does this show **sell getCHRGD products**, or stay product-neutral education with the brand as the trusted voice? **→ default: neutral education, with our product mentioned only where it's honestly the answer — maybe 1 in 4.**
3. Do you want **visible sources** on-slide (a "source: …" line), or sources kept internal for your own confidence? **→ default: internal, with a source line only on the eyebrow-raising numbers.**
4. Where does the material come from — **your product range** (an ingredient library I'd seed from your labels), **audience questions** (comments/DMs), or **the trend scout**? **→ default: an ingredient library as the spine, topped up by real audience questions.**
5. Should this show ever be a **single-slide** post (one fact, no swipe)? The engine supports 1–10. **→ default: yes, occasionally — a single hard fact is very saveable.**
6. Tone: **calm expert** or **blunt myth-buster**? They need different type, palette and hooks. **→ default: calm expert with a sharp edge on marketing lies.**

---

### Show 4 · **THE SESSION** — workouts, with the supplement tied in

*Practical · high-contrast · the most saveable thing you post*

| | |
|---|---|
| **What it is** | A real session someone can screenshot and do. Segmented by who and when, so over a month you cover the whole audience rather than shouting at one slice of it. |
| **Spine** | 1 who it's for + the promise ("30 min, no rack, after work") · 2 the session (the actual sets/reps, readable) · 3 the hard bit + how to not fail it · 4 where the supplement fits — *timing*, not a pitch · 5 the swap for a different level/kit · 6 screenshot-this |
| **Look** | Real gym photography, high contrast, but **type-first**: this is a reference card, so the workout must be readable at thumb size. Dark is fine here — legibility rules. |
| **Cast** | Real people, no mascot. |
| **Wants** | Saves, then shares. KPI: saves per view — the clearest number of the five. |
| **Reuses** | `five_mistakes` / `read_before` skeletons; the `body` block already in the `Slide` model is exactly the dense readable block a workout needs. |
| **Needs new** | **The variant matrix** — the show's whole identity is that it's segmented — plus a rotation memory so you don't do "after-work weights" three weeks running, and a legibility check on the session slide. |

**The variant axes I'd build the picker from** (pick one per axis, engine fills the rest):

- **Who** — lads / girls / mixed / beginner / returning-after-a-break / over-40
- **When** — 5am before work · lunch break (30 min) · the 6pm rush · late-night quiet gym
- **What** — weights · cardio · HIIT · hybrid-Hyrox · mobility/recovery
- **Where** — full gym · hotel/minimal kit · home, no equipment · outdoors
- **Supplement tie-in** — pre-workout (timing) · intra (hydration/endurance) · post (recovery) · everyday (creatine/protein baseline)

**Questions for you**
1. **Who picks the variant?** You choose from the matrix each week, the engine auto-rotates to cover the grid, or it suggests and you confirm? **→ default: suggests-and-you-confirm, driven by what hasn't been covered recently.**
2. **How prescriptive can the workout be?** Named exercises with sets/reps/rest (most useful, most liability), or a structure with the loading left to the reader? **→ default: named exercises with sets and reps, RPE not %1RM, and a plain "scale it" line.**
3. Does the supplement tie-in have to be **ours**, generic ("a pre-workout"), or the *category with our product named once*? **→ default: category first, named once, always as timing advice.**
4. Should each session be **standalone**, or part of a numbered block ("Week 2 of the 6pm-rush plan") that gives people a reason to come back? **→ default: standalone for now — a block is show 2's job.**
5. Are you happy for this show to **not be funny**? It's the one where usefulness beats personality. **→ default: yes. Dry and useful.**
6. **Gendered variants** — comfortable posting "for the girls" / "for the lads" framing, or keep it goal-framed (glutes/strength/conditioning) instead? **→ default: goal-framed, because the algorithm segments better on goal than on gender and it ages better.**

---

### Show 5 · **LIVE WIRE** — today, and our take on it

*Topical · native · unpolished on purpose*

| | |
|---|---|
| **What it is** | The show that already exists. Whatever the UK is actually talking about this week, leapt into a gym angle — the Burnham → "make gyms free" move that the concept engine was built around. |
| **Spine** | 1 the take, stated flat · 2 the receipt (the real thing that happened) · 3 the twist / the gym analogy · 4 the side worth arguing over · 5 pick one, comment |
| **Look** | Native and slightly rough: phone flash, screenshots, notes-app, meme-shaped. This one should look like it wasn't designed. |
| **Cast** | None (occasional Amp cameo only if you unlock cameos). |
| **Wants** | Comments and reach. KPI: comments + reach, and it's the show that will occasionally spike. |
| **Reuses** | Nearly all of it is built: `chrgd/trends.py` (scout), `chrgd/concepts.py` (sketch + develop), the radar lanes, the conceptgate tournament, the "already pitched" freshness memory. |
| **Needs new** | Almost nothing structurally — mostly **narrowing** the current everything-machine down to this one show's job, so the other four stop inheriting its hot-take voice. |

**Questions for you**
1. Is Live Wire **one post a week** (Friday's take), or the **overflow slot** — the thing you post when something big breaks, on top of the five? **→ default: one scheduled slot, plus permission to jump the queue when something genuinely lands.**
2. How **political** is it allowed to be? Current prompt says light-touch, non-partisan, play the analogy not the person. Hold that? **→ default: hold it.**
3. Does it include **fitness-industry** news (a chain's prices, a viral gym drama, a supplement recall) as well as general UK news? **→ default: yes — industry news is safer and more on-brand than politics.**
4. Should this show be allowed to be **ragebait**? The lane exists today. **→ default: opinionated yes, bad-faith no — a take you'd defend out loud.**

---

## 2. The week

| Day | Show | Job |
|---|---|---|
| Mon | **THE SESSION** | useful, sets the week up, saveable |
| Tue | **STRAIGHT UP** | authority |
| Wed | **AMP** | personality, mid-week lift |
| Thu | **THE VILLA** | the appointment — cliffhanger + vote |
| Fri | **LIVE WIRE** | topical, most likely to spike into the weekend |

**Questions for you**
1. Is this five posts a week, one per weekday — or five *types* spread across a different posting frequency? **→ default: five a week, Mon–Fri.**
2. Happy with that day order? Thursday for the Villa deliberately gives the vote the weekend to collect comments. **→ default: yes.**
3. Should the calendar **hard-assign** shows to days (the create screen opens on today's show), or stay free? **→ default: hard-assign with an override — the whole point is an appointment.**

---

## 3. What changes in the engine

The unifying move: introduce a **Show** object, and make everything that's
currently global read from it. Additive — no existing path breaks, and an idea
with no show behaves exactly as today.

```
        ┌── config/shows/<key>.toml ──┐   declarative: spine, voice, look,
        │  the whole show definition  │   cast, gate profile, KPI, weekday
        └──────────────┬──────────────┘
                       │
   ┌───────────────────┼────────────────────┬──────────────────┐
   ▼                   ▼                    ▼                  ▼
create screen      pipeline.py          images.py         conceptgate.py
5 show tiles,   show block + spine    show look pack     show's rubric
each with its   injected into the     overrides the      (comedy / claims /
own 2nd screen  system prompt         house furniture    legibility / hot-take)
```

**New**
- `config/shows/*.toml` — one file per show. Label, tagline, weekday, spine
  (slide roles), a voice block, a look pack (palette, type, motif, style
  preset, and **furniture toggles** — wordmark/scrim/counter/footer on or off
  per show, which is how the Villa stops looking like a CHRGD ad), cast
  (`none` | `amp` | `series:<key>`), gate profile, default engagement play,
  slide-count range.
- `chrgd/shows.py` — loader + `Show` model, mirroring how `chrgd/mechanics.py`
  and `chrgd/brand.py` already load their configs.
- `chrgd/series.py` + series/episode storage — **Villa only**, per
  `STORY_ENGINE_PLAN.md`.

**Changed (all small, all additive)**
- `route_json["show"] = "<key>"` on the idea — the same place `mechanic_lock`
  and the concept gate verdict already ride.
- `pipeline.py` — inject the show's brief + spine alongside the existing
  prompt; the spine constrains slide roles instead of the engine free-picking.
- `images.py` — `compose_design_prompt` takes the show's look pack; furniture
  toggles read from the show before falling back to `brand.toml [identity]`.
- `conceptgate.py` — swap the rubric per show. A comedy beat and a claims
  explainer should not be judged by the same "is this a thumb-stopping hot
  take" scale; that's why the serious ones will currently come out shouty.
- `concepts.py` — `sketch_concepts(show=…)`: five ideas **inside one show's
  format**, instead of five generic ones. This is the biggest quality win in
  the whole plan for the smallest change.
- `/create` — opens on five show tiles (plus a small "off-format" escape to
  the current doors), each leading to its own tailored second screen: Amp →
  pick the drain; Villa → next episode; Straight Up → pick the ingredient or
  question; The Session → the variant matrix; Live Wire → today's concepts as
  they are now.
- `calendar` — show-aware slots, "what's due this week".
- `learning.py` / `analytics.py` — tag performance **by show**, so after a
  month you know which of the five to double down on and which to kill.

**Suggested order**
1. **Show layer + Live Wire + Amp** — mostly re-pointing what exists; proves
   the architecture with the two shows that are nearly built.
2. **Straight Up + The Session** — mostly config + two new gates + the variant
   matrix.
3. **The Villa** — the real build (`STORY_ENGINE_PLAN.md`), last, because it's
   the only one that needs new engine and the one most worth hand-testing
   before automating.

---

## 4. Cross-cutting questions

These change the shape of the build more than any single show.

1. **Look independence.** How far does each show's look diverge? (a) shared
   furniture, different palette/type per show; (b) fully separate identities
   with only the `@getchrgd` handle in common; (c) one look, varied slightly.
   **→ default: (b) for the Villa, (a) for the other four.**
2. **Is a show ever allowed to break format?** A locked spine gives
   recognisability and kills surprise. **→ default: the spine is a floor, and
   the engine can add or drop one slide when the idea genuinely wants it.**
3. **Batching.** Do you want to sit down once a week and make all five
   (a "make my week" button that runs five shows in one go), or one a day?
   **→ default: build the weekly batch — it fits how you actually work, and
   `chrgd run` already chains build → render → export.**
4. **Who edits what?** Should each show's brief be editable in Settings like
   the brand profile is, or should shows stay in config files that I change?
   **→ default: editable in Settings — you'll want to tune the voice weekly.**
5. **How do we know a show works?** Nothing currently ties published
   performance back per format. **→ default: per-show KPI (each show above
   names one) plus a simple 8-week review, and be willing to kill one.**
6. **Naming.** AMP / THE VILLA / STRAIGHT UP / THE SESSION / LIVE WIRE are
   placeholders. Do the shows get names on-screen at all (a title card, a
   consistent slide-1 tag), or are they invisible scaffolding the audience
   only feels? **→ default: name them on-screen. A named recurring show is
   what turns viewers into followers.**
