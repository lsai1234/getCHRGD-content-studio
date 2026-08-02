# Why episode 1 didn't work, and what to do about it

Written against the actual output (slide 1, "8PM: RACK'S GUEST LIST ONLY"), not
against a guess. Three separate problems, and it matters that they're separate
because two are mechanical and one is architectural.

**What genuinely worked and should not be touched:** the art style, the world
(Greggs and Vape Hub through the window, the retail-park night, the BACK SOON
sign), and the CHRGD MULTIVERSE masthead. That's the hard part and it landed.

---

## Problem 1 — the slide is describing its own props. This is a bug.

The second line on the slide reads:

> Velvet rope = lifting straps. Wonky '20:00' sign.

That is an **instruction to the illustrator that got printed on the artwork**.
It's the single biggest reason it reads as AI: the post is explaining its own
set dressing to the reader, in shorthand, with an equals sign.

**Root cause, and it's in the code.** In `ai_design` mode every word of
`headline`, `supporting` and `body` is painted onto the image
(`images.py:555`). And in the output contract (`pipeline.py:55-56`) those two
fields are defined as, in full:

```json
"headline": "string",
"supporting": "string",
```

Every *other* field in that contract gets a paragraph of definition —
`image_prompt` gets nine lines, `swipe_trigger` gets four. The two fields that
end up largest on the finished artwork have no definition at all. So the model
used `supporting` as a notes field, and the renderer printed the notes.

This is not a prompt-quality problem. It's a missing contract.

**Fix (no options needed — this just needs doing):**

- **1a. Define the copy fields.** `headline` and `supporting` get real
  definitions: this is STORY copy a reader reads, never a description of the
  image, never a prop note, never shorthand with `=` or `/`.
- **1b. A copy lint**, in the same shape as the claims lint: deterministic,
  offline, free. It rejects a slide whose copy contains art-direction tells —
  an `=` between two nouns, "wonky/battered/neon sign", "shot", "frame",
  "panel", "in the background", colour notation. A build that trips it gets one
  rewrite, then goes to review. Cheap and it catches this failure *every* time,
  which a prompt instruction won't.

---

## Problem 2 — a character serial with no characters in it

Slide 1 of a show whose entire premise is recognisable characters contains an
empty room. No Tracy Beaker, no Orangina, nobody. The world got drawn instead
of the story.

**Root cause:** the spine's slide-1 brief says "something has CHANGED at Iron
Palace and it is concrete and visible", which the model read as *a change to
the room*. Nothing in the Multiverse path forces a character into frame —
`feature_character` exists on the slide model and defaults to the engine's
judgement, and for a set-dressing brief its judgement was "no person needed".

**Fix (no options needed):**

- **2a. Every Multiverse slide names who is in the panel**, and the renderer
  gets that name plus their locked design. A slide with nobody in it is invalid
  on this show.
- **2b. Slide 1 must open on a character doing something**, never on an
  establishing shot of the gym. Establishing shots are what you do when you
  don't have a story yet.

---

## Problem 3 — it isn't a story. This one is a real choice.

"8pm: Rack's guest list only" is a *premise*, not an *event*. Nobody wants
anything, nobody does anything, nothing is at stake, and there's no question
pulling you to slide 2. The rest of the set inherits that: eight slides
elaborating a concept instead of eight chapters of a plot.

**Root cause, honestly:** the engine is being asked to invent the premise, the
plot, the character beats, the on-slide copy, the caption and the art direction
**in a single call**, while also obeying a spine, a canon, a likeness gate and
a worked example. Something gets dropped, and what gets dropped is the hardest
thing — the story.

I tightened the brief last round and this is what came back. I don't think
another brief rewrite gets there, and I'd rather say that than sell you a third
one.

### Option A — Write the story first, then break it into slides ⭐ recommended

Two calls instead of one. Call 1 writes the **episode as prose** — 150 words,
no slides, no art direction, just: who wants what, what goes wrong, what they
choose, how it ends, what's left hanging. Call 2 takes that finished story and
*cuts it into slides*.

This is your own instruction back to you: *"write a story that's genuinely
clever and people will want to read, and then break it into the slides."*

- **Why it works:** the model does one job at a time. Writing a story is a
  different task from formatting one, and collapsing them is why the story
  loses. It's also the same split that fixed concept quality in Phase 0 — sketch
  first, develop second.
- **Bonus:** you can *read the prose before it renders*. A bad episode is
  visible in ten seconds and costs nothing to reject, instead of costing eight
  images.
- **Cost:** one extra cheap text call per episode. Roughly a session to build.
- **Risk:** low. It's additive — the slide-cutting call is the existing write
  path with the story handed to it.

### Option B — You write the premise, the engine writes the episode

A one-line box on the Multiverse screen: *"Tracy Beaker fixes the lat pulldown
at 3am and won't admit it."* The engine does everything downstream.

- **Why it works:** the premise is the bit that's failing, and it's also the bit
  you're best at and fastest at. Thirty seconds of your time buys the whole
  problem away.
- **Cost:** an afternoon. Much the smallest build here.
- **Risk:** none technically — but it makes the show manual. You'd be writing
  the seed of one post a week forever.
- **Note:** this pairs well with A rather than competing. A does the work when
  you don't have an idea; B lets you steer when you do.

### Option C — A story gate

After the episode is written, a cheap judge reads it **cold** and answers three
questions: *what happened? who wanted what? what made you swipe from 1 to 2?*
If it can't answer, the episode is rewritten. Same pattern as the glance test,
aimed at coherence instead of legibility.

- **Why it works:** it catches "elaborated premise" specifically, because you
  cannot answer "what happened" about a premise.
- **Cost:** half a session. It's the claims-gate shape again.
- **Risk:** a gate can only reject, never invent. On its own it would loop on
  weak stories rather than fix them — it needs A or B in front of it.

### Option D — Hand-write a season, engine renders it

You (or I) write six episodes as prose up front. The engine only ever does
slide-cutting and art.

- **Why it works:** completely removes story generation as a risk.
- **Cost:** a day of writing, then near-zero per episode.
- **Risk:** it isn't a content engine any more, it's a rendering pipeline. Fine
  for proving the format is funny before automating it — which is exactly what
  I'd want to do before trusting A.

---

## What I'd actually do

**1 and 2 regardless** — they're bugs, they're precise, and neither needs a
decision from you.

**Then A + C together**, with **B** as the steering wheel: the engine writes the
story as prose, a gate checks it's actually a story, you can read it before a
penny is spent on images, and you can override the premise whenever you have a
better one.

I'd hold **D** in reserve. If A's prose still isn't funny after a couple of
goes, that's the signal that the format needs a human writer for a season
before it's worth automating — and finding that out cheaply is the point.

---

## What I need from you

1. **Which option** (or combination). My recommendation is 1 + 2 + A + C, with B.
2. **The 8pm nightclub premise** — was that anywhere near a direction you'd
   want, or is it wrong at the concept level too? It changes whether the
   problem is execution or taste.
3. **Do you want to see the prose before the render?** It's the cheapest
   quality gate available and Option A gives it to you for free — but it does
   put a manual step in the middle of the journey.
