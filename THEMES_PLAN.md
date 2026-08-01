# THEMES_PLAN — five shows, five journeys, one engine

**Status: draft v0 — written to be argued with.** Every section ends with
*Questions for you*; my recommended default is marked **→ default** so you can
reply "default" and move on where you don't care.

## Decisions locked (round 1)

| # | Decision | Consequence |
|---|---|---|
| D1 | **The parody show is a serial** (then THE VILLA, now THE MULTIVERSE — see D6) — recurring characters, cliffhanger + a vote that becomes canon next episode | `chrgd/series.py`, series/episode storage and the continuity gate get built (the `STORY_ENGINE_PLAN.md` build). Episodes can't be pre-batched ahead of a vote. |
| D2 | **Straight Up is educational + sourced** — say what the evidence supports, cite internally, never promise an outcome; opinionated about the *industry* (pricing, labelling, marketing), never about physiology | The show's gate is a **claims gate**, not the hot-take rubric: a separate pass that fails outcome promises and unsourced numbers. |
| D3 | **The parody show breaks the house look; the other four share the furniture** with their own palette, type and photography | Furniture toggles (wordmark / scrim / counter / footer) move into the show's look pack, defaulting to `brand.toml [identity]`. The Multiverse runs handle-only. |
| D4 | **Build order: Amp + Live Wire first** | Phase 1 is the Show layer proved against the two shows that are already ~80% built. Straight Up + The Session next, The Multiverse last. |

### Round 2

| # | Decision | Consequence |
|---|---|---|
| D5 | **Amp is tip-led**; stories must still be useful. He plays himself, but his state ranges freely — deflated and low one week, beaming and full of colour the next. He may appear in other shows where useful, and he is the **video pilot** | `charge_arc()`'s forced rising-charge arc becomes *one* Amp spine, not the mandatory one; his state becomes a per-slide expression + palette system. Amp's gate adds a **usefulness** check. M6 turns on against Amp first. |
| D6 | **The Villa becomes THE MULTIVERSE** — a comic universe of *recognisable* characters: public figures (caricatured) plus AI-slop meme characters (Orangina and family). The engine remembers past storylines; each episode extends canon but still lands cold for a newcomer | Needs a **character roster store** (locked comic visual + trait + safety class per character), a **canon/events log**, a **recap generator**, and a **likeness gate**. "Love Island but at the gym" becomes a *season* the roster is cast into, not the show itself. |
| D7 | **Straight Up stays general** category education; own-brand products are a later enhancement | The ingredient library carries an empty `our_product` slot per entry from day one, so wiring a range in later is filling a field, not a re-architecture. |
| D8 | **The Session isn't funny** — informative and saveable | Its gate scores usefulness and legibility, not humour or hot-take heat. |
| D9 | **Live Wire is the overflow lane**, weighted to big gym news and new science, and retargeted from "UK trending" to **what 18–30 UK gym-goers are actually into** (reality TV, football, holidays, money) — fitness-adjacent, not fitness-only | The scout gains an **audience-interest territory model** in place of a generic country-wide trend scan. This is the substantive change to `trends.py`. |
| D10 | **Shows are named on screen** | Each show's look pack carries a title treatment; a show tag becomes part of its furniture. |

### Round 3

| # | Decision | Consequence |
|---|---|---|
| D11 | **The Session is goal-framed** (glutes / strength / conditioning / fat loss / mobility), never gender-labelled — but sessions are free to skew in who they appeal to, and **no-equipment is a standing axis**, not a fallback | The variant matrix's "Who" axis becomes **Goal**; targeting lives in the content, not the caption. |
| D12 | **Coverage nudges, it never enforces** | The engine tracks what's gone stale and suggests; the operator can run two of the same type in a week without a fight. Applies to The Session's matrix and Amp's situation bank alike. |
| D14 | **The Session must be doable by anyone** — named exercises, sets and reps, effort in plain words, no %1RM or RPE scales | The show's brief bans training jargon that assumes history; the legibility gate checks a beginner could follow it unaided. |
| D16 | **Multiverse canon is authored in the studio** — the operator creates and edits storylines and history directly, with a hard **reset** available | The canon/events log is operator-editable, not engine-only. The audience vote becomes optional rather than the mechanic the serial depends on, so episodes can be planned ahead. |
| D15 | **Roster locked at 12** — Orangina in (half human, half orange, the wellness girl), Clavicular dropped, Bonnie Blue left out | `MULTIVERSE_ROSTER.md` is final; it becomes `config/roster.toml` in Phase 3. |
| D13 | **Roster approved with swaps** — Joe Wicks → Tracy Beaker, Ronnie Coleman → Clavicular (pending), David Goggins → Bonnie Blue (flagged), in `MULTIVERSE_ROSTER.md` | Becomes `config/roster.toml`. Iron Palace (Unit 4, a retail park) is the fixed location; the series engine is "the one place in the multiverse where nobody gets special treatment". |

---

> **The engineering plan now lives in [`BUILD_PLAN_SHOWS.md`](BUILD_PLAN_SHOWS.md)** — phases, sizings and the actual files each change touches.

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
| **What it is** | **Tip-led.** Amp teaches one genuinely usable thing through a gag. Story weeks still exist, but a story that isn't useful to the viewer doesn't ship — "useful" is a gate condition, not a nice-to-have. |
| **Spine** | 1 the relatable state (drained, wired, buzzing — whatever the post needs) · 2 it gets worse / the pull · 3 the turn — **the tip itself, stated plainly** · 4 it's working · 5 the payoff state + "Stay amped." |
| **Look** | Flat vector, sticker energy, bold outlines. **Amp's colour and state carry the emotion** — a dim grey-blue slump one week, full-saturation beaming the next. No photoreal, ever. |
| **Cast** | Amp, solo, locked (`chrgd/character.py`). He plays himself — no supporting cast for now. |
| **Wants** | A share and a save. KPI: shares plus saves — the tip is what earns the save. |
| **Reuses** | `character.py` (built), `amp_charge_cycle` (built), the Amp slot in the concept sketch prompt (built). |
| **Needs new** | The **usefulness gate** (every Amp post must name the one thing the viewer takes away); an **expression/palette system** replacing the strict rising-charge arc; a **situation bank** so he isn't drained by leg day every week. |

**What changes from what's built.** `charge_arc()` currently forces a
monotonically rising charge % across the slides — Amp always starts flat and
ends charged. You want his state to be free: sometimes deflated and low,
sometimes beaming and full of colour. So the charge cycle stops being the
mandatory arc and becomes **one of Amp's spines**, while his emotional state
becomes a per-slide expression + palette system the story drives. The locked
character prefix stays exactly as it is — that's what keeps him on-model.

**The sameness risk still stands.** One character weekly needs new *situations*
each time (4am airport, the 3pm slump, a broken lift, January in a packed gym,
a shaker leaking in a gym bag). I'd store the situation bank as a tickable
list, same idea as the "already pitched" memory in `concepts.py`.

**Questions for you**
1. ~~Story vs Tip~~ **DECIDED (D5): tip-led, and stories must be useful too.**
2. ~~Cameos~~ **DECIDED (D5): Amp can appear in other shows where he's useful** — e.g. fronting a Straight Up explainer. Should there be a *limit* (say never more than one non-Amp post a week), or judge it case by case? **→ default: case by case, but never in THE MULTIVERSE as a lead.**
3. ~~Video pilot~~ **DECIDED (D5): yes, Amp is the M6 pilot.**
4. Does Amp **talk** (speech bubbles) or is it narration + his face? **→ default: speech bubbles, sparingly.**
5. How **technical** can Amp's tips get? A form cue and a timing trick are easy to draw; "creatine loading protocols" is not. **→ default: keep Amp on behaviour and habit tips, and send the biochemistry to Straight Up.**

---

### Show 2 · **THE MULTIVERSE** — the weirdest crossover in fitness (comic serial)

*Fun · comic-book · zero brand furniture · the one people follow for*

> Renamed from THE VILLA. It isn't one format any more — it's a **world with a
> roster**, and "Love Island but at the gym" becomes one *season* the roster
> gets cast into rather than the whole show.

| | |
|---|---|
| **What it is** | A recurring comic universe where instantly recognisable characters collide in gym storylines. Two kinds of cast: **recognisable public figures** (caricatured) and **AI-slop meme characters** (Orangina and that whole brainrot family). The joke is the collision — the world's weirdest multiverse, and it's set in a PureGym at 6pm. |
| **Spine** | 1 cold-open recap that works for a newcomer · 2 the situation + the threat · 3 the complication · 4 escalation · 5 the turn · 6 cliffhanger + the hook into next week |
| **Look** | **Comic book.** Bold ink outlines, halftone shading, saturated flat colour, panel captions and speech bubbles, characters *clearly* recognisable at thumb size. No near-black, no scrim, no wordmark — handle only (D3). |
| **Cast** | A **roster**, not a fixed cast. Each character carries a locked comic-style visual prompt (the `character.py` locked-prefix trick, one per character), a joke-generating trait, and a **safety class**. Each episode casts 2–4 of them. |
| **Wants** | Comments and returns. KPI: comment volume + week-2 return rate. |
| **Reuses** | `STORY_ENGINE_PLAN.md`'s stage split (bible → arc → beats → copy → continuity gate) and the locked-character pattern from `character.py`. |
| **Needs new** | `chrgd/series.py` + a **character roster store** + a **canon/events log** so episode N+1 genuinely extends N; the continuity gate; the recap generator; and a **likeness gate** (below). The only show needing real new engine. |

**Canon memory — the thing that makes it a universe.** Every episode writes
what changed to an events log: who won, who lost, who now hates whom, what's
unresolved. The next episode reads that log, so storylines compound. But slide
1 is always a cold-open recap written for someone who's never seen it — the
recap does the work, the canon does the reward. That's the "extends the story
but still makes sense to a newcomer" requirement, and it's mechanical enough to
enforce in code.

**One thing I want to flag before it gets built.** Recognisable public figures
in AI-generated comics, on a supplement brand's account, is the one part of
this plan with genuine outside risk — personality/publicity rights, and TikTok's
own rules on synthetic likenesses of real people. It is very workable, and
parody of public figures in caricature is well-trodden ground; it just needs
lines drawn *in the engine* rather than remembered each week. So I'd encode a
**likeness gate** for this show:

1. **Caricature, never photoreal.** The comic style you asked for is also the
   safety line — a stylised drawing reads as commentary, a photoreal render of
   a real person reads as a fake.
2. **No endorsement, ever.** A real person is never shown using, holding or
   recommending a getCHRGD product, and the brand furniture stays off these
   posts anyway (D3). Implied endorsement is the sharpest legal edge and the
   easiest one to simply not go near.
3. **No fabricated statements presented as real.** Absurd situations, yes;
   realistic quotes, fake announcements or news framing, no.
4. **Punch at status and situation** — never at appearance, protected
   characteristics, or anything sexual, criminal or health-related.
5. **Living public figures only, no private individuals, no minors.**
6. **A roster you approve.** The engine casts from your allow-list; it never
   free-picks a person.
7. **Labelled** — a visible "parody" tag in the show's furniture plus the
   platform's AI-content toggle on publish.

The AI-slop meme characters are the safest and probably funniest half of the
roster — nobody has publicity rights in Orangina — so I'd weight episodes
toward them and use the real-person caricatures as guest stars.

**Questions for you**
1. ~~Serial or standalone?~~ **DECIDED (D1): serial** — now a universe with canon memory (D6).
2. **The roster is the show — name it.** Give me 8–12 you want in the world: which public figures, and which meme/AI-slop characters. **→ default if you'd rather I draft: a roster weighted ~2:1 toward meme characters, with fitness-adjacent public figures as guests, for you to cut.**
3. **Seasons or open world?** Does a format arc run ~6–8 episodes (the villa season, then a tournament season, then the Hyrox-boss mockumentary) or is it a new collision every week in a persistent world? **→ default: seasons — an arc gives the cliffhanger somewhere to go.**
4. **The vote** — still want the audience choosing what happens next (canon), or is the cliffhanger enough on its own? **→ default: keep the vote; it's the strongest comment driver you have.**
5. Is **Amp** in this universe (D5 lets him appear where useful) — a recurring bit-part in the multiverse, or does he stay out? **→ default: rare cameo, never the lead.**

---

### Show 3 · **STRAIGHT UP** — supplements, facts, actual help

*Serious · clean · trustworthy · the show that sells*

| | |
|---|---|
| **What it is** | The honest answer to a question people genuinely search: what creatine actually does, whether you need EAAs, what "proprietary blend" hides, when protein timing matters and when it doesn't. No hype, no fear. |
| **Spine** | 1 the question, in their words · 2 the short answer (given away immediately — trust beats tease here) · 3 why / the mechanism, plainly · 4 what it means for you (dose, timing, practicalities) · 5 what to ignore / the myth · 6 save-this |
| **Look** | Bright, clean, editorial. Light background or crisp product-on-seamless, generous whitespace, one accent, large legible type. The *opposite* of the gritty night-flash look. Legibility is the aesthetic. |
| **Cast** | None by default — authority comes from the plainness. Amp may front a post where a gag genuinely helps the explanation (D5), but never where it undercuts the evidence. |
| **Wants** | Saves + follows. KPI: saves and profile visits. |
| **Reuses** | The `facts` door (paste research → angles) is already exactly this journey's front end; `myth_fact` / `insider_mechanism` in `config/mechanics.toml`; the `claim_safety` QA score already in the contract. |
| **Needs new** | A **claims gate** with teeth (a separate pass, not one score inside a self-audit); a source/citation field carried through to the slides; an ingredient/product library so the show has a backlog rather than being invented weekly. |

**The thing that decides this show:** on TikTok, a UK supplement brand making
health claims is a real regulatory and platform risk, and "hedged into
uselessness" is the failure mode on the other side. So the gate needs an
explicit, written line — see the question below, it's the one I most need you
to answer.

**Built general now, product-aware later (D7).** There are no getCHRGD
products to talk about yet, so the show is category education: ingredients,
mechanisms, marketing lies. The important part is that I build the ingredient
library with an **empty `our_product` slot on every entry** from day one — so
when you do have a range, wiring it in is filling a field, not re-architecting
the show. I'll design for that hook and leave it unpopulated.

**Questions for you**
1. ~~How far do we go on claims?~~ **DECIDED (D2): educational + sourced.**
2. ~~Sell products?~~ **DECIDED (D7): general category education for now, product hook designed in and left empty.**
3. Do you want **visible sources** on-slide (a "source: …" line), or sources kept internal for your own confidence? **→ default: internal, with a source line only on the eyebrow-raising numbers.**
4. Where does the material come from — an **ingredient library** I seed (creatine, caffeine, beta-alanine, EAAs, electrolytes, ashwagandha, collagen…), **audience questions**, or **the science feed from Live Wire**? **→ default: the ingredient library as the spine, topped up by audience questions.**
5. Should this show ever be a **single-slide** post (one fact, no swipe)? **→ default: yes, occasionally — a single hard fact is very saveable.**
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

**Goal-framed, not gender-framed (D11).** The framing on the slide is always
the goal — glutes and lower body, upper-body strength, conditioning, fat loss,
mobility. That doesn't stop a session naturally appealing more to one group: a
glute-focused session finds its audience without ever needing a "for the girls"
label on it, and it stays useful to everyone else. The targeting happens in the
*content*, not the caption.

**The variant axes** (pick one per axis; the engine fills the rest):

- **Goal** — lower body/glutes · upper-body strength · full-body conditioning · fat loss · endurance · mobility & recovery
- **When** — 5am before work · lunch break (30 min) · the 6pm rush · late-night quiet gym
- **How** — weights · cardio · HIIT · hybrid/Hyrox · circuits
- **Where** — full gym · minimal kit (hotel, home rack) · **no equipment at all** · outdoors
- **Level** — first month · returning after a break · experienced
- **Supplement tie-in** — pre-workout (timing) · intra (hydration/endurance) · post (recovery) · everyday baseline

**No-gym-needed is a first-class axis, not a fallback.** It's the variant with
the biggest addressable audience — people who haven't joined a gym yet, people
away for work, January — and it should come round regularly rather than being
the thing we do when we've run out of ideas.

**Coverage is a nudge, not a rota (D12).** The engine tracks what's been
covered and *suggests* what's gone stale ("no no-equipment session in four
weeks"), but it never blocks or forces. If you want two lower-body sessions in
a week because that's what's landing, you do two.

**Questions for you**
1. ~~Who picks the variant?~~ **DECIDED (D12): the engine suggests from what's gone stale, you pick — and you're free to ignore it entirely.**
2. ~~How prescriptive?~~ **DECIDED (D14): easy for anyone.** Named exercises, sets and reps, and effort described in plain words ("stop with about 2 reps left in you") — no %1RM, no RPE scale, no jargon that assumes training history.
3. ~~Supplement tie-in~~ Category-only for now (D7); revisit when you have a range.
4. Should each session be **standalone**, or part of a numbered block ("Week 2 of the 6pm-rush plan")? **→ default: standalone — the serial is show 2's job.**
5. ~~Not funny?~~ **DECIDED (D8): correct — informative and saveable, no jokes.** The gate for this show scores *usefulness and legibility*, not humour or hot-take heat.
6. ~~Gendered variants~~ **DECIDED (D11): goal-framed**, with sessions free to skew in appeal; **no-equipment is a standing axis**; coverage suggests rather than enforces (D12).
7. Nothing outstanding on this show — D8, D11, D12 and D14 close it out.

---

### Show 5 · **LIVE WIRE** — what our lot are actually talking about

*Topical · native · unpolished on purpose · the overflow lane*

| | |
|---|---|
| **What it is** | Not "UK trending". **What a 18–30 UK gym-goer is actually into this week** — which is big gym and fitness news, the latest science worth knowing, and the culture that sits alongside the gym in the same person's life: reality TV, football, holidays, nights out, money. Fitness-adjacent, not fitness-only. |
| **Spine** | 1 the take, stated flat · 2 the receipt (the real thing that happened) · 3 the twist / the gym angle · 4 the side worth arguing over · 5 pick one, comment |
| **Look** | Native and slightly rough: phone flash, screenshots, notes-app, meme-shaped. Should look like it wasn't designed. |
| **Cast** | None, usually. |
| **Wants** | Comments and reach. The show most likely to spike. |
| **Reuses** | `chrgd/trends.py` (scout), `chrgd/concepts.py` (sketch + develop), the radar lanes, the conceptgate tournament, the freshness memory. |
| **Needs new** | **A retargeted scout.** This is the real work here — see below. |

**Why the current trending feels generic, and the fix.** `trends.py` scouts
*UK topical* signal and `concepts.py` leaps it into a gym angle. Both stages are
good; the **input** is the problem. A generic UK-trending scan returns the news
everyone's posting, so the leap starts from material your audience has already
scrolled past twice.

The fix is to give the scout an **audience-interest model** instead of a
country: a set of named **territories** it scans, weighted, rather than "what's
trending in the UK". Draft territories:

| Territory | What it pulls | Weight |
|---|---|---|
| **Gym & fitness news** | chain prices, viral gym drama, a supplement recall, a Hyrox/marathon moment | heavy |
| **The science** | a new study worth knowing, plainly explained, honestly caveated | heavy |
| **Reality TV & telly** | the Love Island / big-format moment everyone's watching | medium |
| **Football** | the weekend, the transfer, the fitness angle | medium |
| **Holidays & summer** | the "shredded by June" cycle, airport gyms, all-inclusives | seasonal |
| **Money & going out** | payday, cost of a night out vs a membership, meal deals | medium |

The gym leap still applies — but now the raw material is something your
audience already cares about, so the leap lands instead of feeling forced.

**Overflow, not a fixed slot (D9).** Live Wire jumps the queue when something
genuinely lands, rather than waiting for its day. Practically that means the
week has four appointment shows and Live Wire fills the fifth slot by default
but can bump any of them.

**Questions for you**
1. ~~Overflow or scheduled?~~ **DECIDED (D9): overflow lane, and weighted toward big gym news + science.**
2. **The territories above — right list?** Add, cut or re-weight. **→ default: as drafted, with gym news and science heaviest.**
3. When Live Wire bumps another show, does the bumped show **slip a day** or **get skipped that week**? **→ default: slip, so each show still gets its turn.**
4. How **political** is it allowed to be? Current prompt says light-touch, non-partisan, play the analogy not the person. Hold that? **→ default: hold it.**
5. Should it be allowed to be **ragebait** (the lane exists today)? **→ default: opinionated yes, bad-faith no — a take you'd defend out loud.**

---

## 2. The week

Four appointment shows, plus Live Wire as the overflow lane (D9) — it takes the
fifth slot by default and can bump any of the others when something genuinely
lands.

| Day | Show | Job |
|---|---|---|
| Mon | **THE SESSION** | useful, sets the week up, saveable |
| Tue | **STRAIGHT UP** | authority |
| Wed | **AMP** | personality + the tip, mid-week lift |
| Thu | **THE MULTIVERSE** | the appointment — cliffhanger, and the weekend to collect comments |
| Fri | **LIVE WIRE** | topical, most likely to spike into the weekend |

**Questions for you**
1. Is this five posts a week, one per weekday — or five *types* at a different posting frequency? **→ default: five a week, Mon–Fri.**
2. Happy with that day order? **→ default: yes — Thursday gives the Multiverse cliffhanger the weekend.**
3. Should the calendar **hard-assign** shows to days (create opens on today's show), or stay free? **→ default: hard-assign with an override — the appointment is the point.**

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
- `config/shows/*.toml` — one file per show. Label, **on-screen title
  treatment** (D10), tagline, weekday, spine (slide roles), a voice block, a
  look pack (palette, type, motif, style preset, and **furniture toggles** —
  wordmark/scrim/counter/footer on or off per show, which is how the Multiverse
  stops looking like a CHRGD ad), cast (`none` | `amp` | `roster:<key>`), gate
  profile, default engagement play, slide-count range.
- `chrgd/shows.py` — loader + `Show` model, mirroring how `chrgd/mechanics.py`
  and `chrgd/brand.py` already load their configs.
- `chrgd/roster.py` + a character store — **Multiverse (D6)**: per character a
  locked comic-style visual prompt, the joke-generating trait, and a **safety
  class** (`public_figure` | `meme_character` | `original`) that drives which
  rules apply to them.
- `chrgd/series.py` + a **canon/events log** — what changed each episode, so
  episode N+1 reads N; plus the cold-open recap generator that keeps it
  legible to a newcomer.
- **Gate profiles** — the one rubric becomes several, selected per show:
  comedy + usefulness (Amp), continuity + likeness (Multiverse), claims
  (Straight Up), usefulness + legibility (The Session), hot-take (Live Wire).
- **The interest-territory model** for the scout (D9) — named, weighted
  territories replacing the generic UK-trending scan.

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
- `trends.py` — scouts the show's **territories** rather than "what's trending
  in the UK" (D9); the leap in `concepts.py` is unchanged, it just gets better
  raw material.
- `/create` — opens on five show tiles (plus a small "off-format" escape to
  the current doors), each leading to its own tailored second screen: Amp →
  pick the situation + his state; Multiverse → next episode (who's in it, what
  canon says); Straight Up → pick the ingredient or question; The Session →
  the variant matrix; Live Wire → today's concepts, retargeted.
- `calendar` — show-aware slots, "what's due this week".
- `learning.py` / `analytics.py` — tag performance **by show**, so after a
  month you know which of the five to double down on and which to kill.

**Build order — DECIDED (D4)**
1. **Show layer + Live Wire + Amp** — mostly re-pointing what exists; proves
   the architecture with the two shows that are nearly built.
2. **Straight Up + The Session** — config + the claims gate (D2) + the variant
   matrix.
3. **The Multiverse** — the real build (`STORY_ENGINE_PLAN.md`), last, because it's
   the only one that needs new engine (D1) and the one most worth hand-testing
   before automating.

---

## 4. Cross-cutting questions

These change the shape of the build more than any single show.

1. ~~Look independence.~~ **DECIDED (D3): the Multiverse gets its own world**
   (handle only, no wordmark/scrim/counter/footer); the other four share the
   brand furniture with their own palette, type and photography style.
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
6. ~~Naming.~~ **DECIDED (D10): named on screen.** Each show gets a title
   treatment in its look pack. Still open: are **AMP / THE MULTIVERSE /
   STRAIGHT UP / THE SESSION / LIVE WIRE** the actual names, or placeholders
   you want to re-christen? **→ default: keep them; they're short, they fit a
   slide tag, and they say what they are.**
