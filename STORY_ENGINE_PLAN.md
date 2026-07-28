# The Story Engine — serialised, gripping gym stories (next phase)

The concept engine makes **one-post ideas**: a leap, a hook, a payoff, done. This
is the other thing — **stories**, where every slide makes you need the next one,
and where the same cast comes back next week.

The operator's two seeds:
- **"Love Island but at the gym."** A reality-format parody: a cast, a villa (a
  PureGym at 6pm), couplings, recouplings, a villain, a public vote.
- **"The Office, but Michael's into Hyrox."** A mockumentary character comedy: a
  boss who has discovered Hyrox and supplements and will not shut up about it.

Both are *serials*, not posts. That's the whole design problem: a serial needs a
cast that stays consistent, a plot that goes somewhere, and an episode shape that
ends on a question. None of that survives being asked for in a single prompt.

---

## 0. Why one prompt can't do this

Asking a model for "a funny 8-slide Love Island gym story" reliably produces:
a premise restated eight times, characters whose names and traits drift, jokes
that are *about* comedy rather than funny, and an ending that resolves nothing
because nothing was ever set up. The failure isn't the model — it's that four
different jobs (premise, cast, plot, slide craft) were collapsed into one.

So the engine splits them. Each stage is cheap, checkable, and reusable:

```
SERIES BIBLE ──► EPISODE ARC ──► BEAT SHEET ──► SLIDE COPY ──► CONTINUITY GATE
  (once)          (per post)      (per post)     (per post)      (per post)
   cast,           what changes    the swipe      the actual      does it match
   world,          this episode    rhythm         words           the bible?
   rules
```

The bible is written **once** and stored. Everything after it is per-episode and
fast, because it's working against a fixed cast instead of inventing one.

---

## 1. Stage 1 — the Series Bible (written once, edited by hand)

One record per series. This is the thing that makes episode 7 feel like episode 1.

**Cast** (3–5 people, no more — a carousel can't hold more):
- name, one-line archetype, **the one trait that generates jokes**
- their want (what they're chasing) and their flaw (what stops them)
- a visual lock — the same character-image discipline `chrgd/character.py`
  already applies to Amp, so the cast doesn't redesign itself every render

**World rules**: where it happens, what the format's conventions are (the
coupling, the weigh-in, the appraisal), what's off-limits.

**The engine of the series**: the thing that renews conflict forever. Love
Island's is *desire + a limited number of racks*. The Office's is *a boss who
believes he's beloved*. Get this wrong and the series dies at episode 3.

**Voice**: the narration register. Reality-TV voiceover is a different instrument
from mockumentary talking-head, and mixing them is why parody goes flat.

> **Brand-safety note, worth deciding up front.** Don't use the actual show names,
> logos, or characters — "Love Island" and "Michael Scott" are protected, and
> TikTok's rights-holder enforcement is real. Parody the *format*, own the cast:
> "The Villa" at a gym with your own islanders; a mockumentary boss who is
> clearly your own creation. Homage travels; a knock-off gets pulled and takes
> the account's reach with it. This costs nothing creatively — the humour lives
> in the format and the characters, not the trademark.

## 2. Stage 2 — the Episode Arc (per post)

Against the bible, decide only **what changes this episode**. One sentence:
"Dave finally asks for a spot, and gets it from the one person he was avoiding."

Constraints that keep a serial serialised:
- exactly ONE status change per episode (someone gains, loses, or learns)
- it must be caused by a character's flaw, not by coincidence
- it must open the NEXT question — the thing the audience now needs to know

## 3. Stage 3 — the Beat Sheet (per post)

The arc becomes slides. Each slide gets a *job*, and the job is the grip:

| Slide | Job | The mechanic |
|-------|-----|--------------|
| 1 | The situation + the threat | State the stakes in one line. ~90% of reach lives here. |
| 2 | Complication | The thing that makes it worse. |
| 3–n | Escalation | Each slide raises cost, never repeats it. |
| n−1 | The turn | The reveal that reframes what came before. |
| n | Payoff + the next question | Resolve *this* beat, open the next one. |

**The swipe rule**: every slide must end mid-tension — a question, an
interruption, a reveal half-shown. If a slide's last line is a complete thought,
the swipe stops there. This is the single most testable property in the whole
plan, and the one worth enforcing in code.

**The funny rule** (from the concept engine, applies here too): specificity is
the joke; set an expectation, snap it; punch up. If you can't name the joke in
one line, the beat isn't funny yet.

## 4. Stage 4 — Slide Copy

Write the words against the beat sheet, in the bible's voice, with the cast's
established traits. Nothing here invents plot — it's execution of an approved
shape, which is exactly why it comes out coherent.

## 5. Stage 5 — the Continuity Gate (the "does it make sense" check)

An independent pass, in the same spirit as the existing slide-1 concept gate:

1. **Continuity** — do names, traits and established facts match the bible and
   the previous episode? (Cheap, mechanical, catches the drift that kills serials.)
2. **Grip** — does every slide but the last end on an open loop?
3. **Sense** — does the causal chain hold, or did a slide skip a step?
4. **Comedy** — can the joke of each intended-funny beat be named in one line?

Fail → sharpen and re-run, bounded rounds. Same pattern as
`CHRGD_CONCEPT_GATE_ROUNDS`, and the same reason: cheap text calls guarding an
expensive render.

## 6. Interaction — the part that makes it a series

A serial gets its reach from the comments, so build the ask into the format:
- **predict**: "who's getting recoupled? Comment before Thursday."
- **vote**: two options at the end, next episode honours the winner — this is
  the strongest one, because the audience's choice becomes canon
- **tag**: the character everyone knows in real life
- **the recap hook**: slide 1 of every episode should work for someone who
  missed the last one, without boring the people who didn't

## 7. How it lands in this codebase

Additive, matching what's already here — nothing below replaces an existing path:

- `chrgd/series.py` — the bible: load/save, cast, world, voice. Mirrors
  `character.py`, which already proves the locked-character pattern with Amp.
- `series` + `episode` tables (or `route_json` on the idea for v1) — the bible,
  and each episode's arc, so episode N+1 can read what N established.
- Job kinds `series_bible`, `episode` on the fast worker; the beat sheet and
  copy are the existing build path with a different brief.
- Renders reuse Amp's locked-prefix trick per cast member, so faces stay put.
- Create screen: a "📺 Series" door alongside the concept engine — pick a series,
  see "next episode", one tap to build. The concept engine stays the daily
  driver; the series is the recurring appointment.

## 8. Suggested order of build

1. `series.py` + storage + a hand-written first bible (fastest way to find out
   if the format is funny is to write one yourself, not to generate it)
2. Episode arc + beat sheet stages, output visible in the UI before any render
3. Continuity gate
4. The vote mechanic, since it's the reach multiplier
5. Series bible *generation* last — by then you'll know what a good one contains

**Test it cheaply first:** write one bible and three episodes by hand, post them,
and see whether episode 2 keeps the audience from episode 1. If retention across
episodes isn't there, no amount of engine fixes it — and you'll have spent a
week's evenings rather than a phase of build.
