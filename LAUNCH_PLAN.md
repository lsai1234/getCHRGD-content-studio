# The launch plan — TikTok, from 70 followers

What the studio is doing for the fortnight either side of go-live, why it's
shaped this way, and the four commands that run it.

---

## The situation, stated honestly

- Launching in ~1–2 weeks: a **free quiz** that reads your training, goals and
  budget and builds a **personalised supplement stack**, orderable once or as a
  plan, at **getchrgd.co.uk**.
- **~70 followers.** Carousels only — nobody on camera.
- Pre-launch goal: **reach and followers**. Post-launch: **quiz completions**.

## Five decisions everything else follows from

**1. There is no countdown.** At 70 followers a "3 days to go" post reaches
almost nobody who has any reason to care. A stranger cannot count down to a
brand they met eleven seconds ago. So the pre-launch fortnight sells nothing
and mentions nothing — it is spent entirely on reach.

**2. Pre-launch content plants the question the quiz answers.** Every post in
the `prime` phase is built to leave someone slightly less certain that their
current supplement routine makes sense. That confusion is the market, it is
genuinely how most people feel, and naming it out loud is both the highest-
reach content this brand can make and the thing that makes a recommendation
quiz obviously worth doing later. The priming is free — it rides inside content
that was worth posting anyway.

**3. We promote the quiz, never the supplements.** The stock is lines anyone
can buy anywhere; there is no story in it. The quiz is yours, it is free, it
takes a minute, and it answers the one question this audience actually has. It
is a *far* cheaper ask than a purchase — a stranger won't buy a tub off a new
account, but they will answer six questions about themselves, because that is a
normal thing to do on the internet and the answer is about them. **The store is
what happens after the quiz.** This is the "most efficient way" you asked for.

**4. The quiz's format IS the content format.** "Answer this, find out which
one you are" is already one of the best-performing shapes on the platform.
A carousel that makes someone privately diagnose themselves reaches people cold
whether or not they ever click — *and* it's a live demo of the product. One
format doing the reach job and the sales job is the only affordable answer at
this size. That's what **THE STACK** is.

**5. The account earns the right to sell by telling people to buy less.**
Roughly half the launch content cuts something — bins a category, tears down a
price, says "you probably don't need this". An account that only recommends
buying more is not one anybody believes when it eventually recommends
something. This is also why the quiz post that converts best is the one saying
*it will happily tell you to take nothing*.

---

## The four phases

**There is no launch date yet, and nothing invents one.** The studio runs
*pinned*: `pin_phase = "buildup"` in `config/campaign.toml` names the phase
everything is written for, and it stays there indefinitely — which is exactly
right, because `prime` is pure reach with no selling and no mention of a launch.

When you have a date: set `launch_date`, clear `pin_phase`, and the phases below
resolve from it. Each is an offset, so moving the launch four days moves the
whole calendar four days. A pin always beats the calendar, so a forgotten one is
loud (`PINNED` on the create screen and in `chrgd campaign status`) rather than
silently writing pre-launch copy through launch week.

| Phase | Days | Sells? | The ask | What it's for |
|---|---|---|---|---|
| **buildup** | *pinned* | No | **follow** | Reach, plus one line at the end teasing what's being built. **Where the studio is now.** |
| **prime** | −14 → −4 | No | comment | Pure reach, no tease at all. |
| **tease** | −3 → −1 | Barely | follow | Earns the swipe, then says the thing exists. |
| **launch** | 0 → +7 | Yes | the quiz, domain in plain text | Same reach content, one ask bolted on. |
| **sell** | +8 → | Yes | quiz by default, plan occasionally | The steady state. |

The phase brief is injected into **every** write call, so a Tuesday STRAIGHT UP
knows it's launch week without you re-briefing it.

### Two rules the engine enforces rather than trusts

- **`prime` cannot sell.** The quiz, the domain and any mention of a launch are
  in that phase's `banned` list. A test asserts no `prime` seed contains the
  domain.
- **"link in bio" is banned during launch.** A first-time FYP viewer is not
  going to your profile to hunt for a link. The domain gets written out in
  plain text where it can be read and typed.

---

## The content

**29 posts are written and queued-ready** in `config/launch_backlog.toml` —
11 prime, 3 tease, 8 launch, 7 sell. Each carries a hook direction, a brief, a
show, a mechanic and a phase, so a build starts from a specific angle rather
than a topic.

### Four shows carry the launch — and two sit it out

The brand is not a supplement brand; anyone can ship the same tubs. What
getCHRGD sells is **judgment**: "tell me what's actually worth taking." So the
launch shows are all acts of judgment, and the content *is* the product running
in public.

| Show | Asks first | KPI |
|---|---|---|
| **⚖️ VERDICT** | What are we ruling on — and what's the ruling? | saves |
| **🧾 RECEIPTS** | What's the product, and what does it cost? | shares |
| **🧪 THE STACK** | What are we sorting on? | shares |
| **🧫 STRAIGHT UP** | Which ingredient? | saves |

Different KPIs on purpose: with no rated posts yet there is **no data** on what
works for this account, so the honest move is to ship a small set and let the
learning loop discriminate. Four formats, four signals, then expand what wins.

**AMP and THE MULTIVERSE are paused for the launch** (`paused_shows` in
`config/campaign.toml` — dimmed on the create screen, never disabled). They're
affinity formats built for a brand with no product, and they pay off over
months of returning viewers, which is the one thing a 70-follower account
launching in a fortnight hasn't got. Amp's tips are behavioural rather than
about what to take; the Multiverse is a serial that rewards people who saw the
last one. They come back once there's revenue to protect.

### The editor owns the facts, the engine owns the craft

This is what makes the two new shows trustworthy, and it's enforced in code:

- **VERDICT** never picks the verdict. You do, on its screen. A brand's ruling
  on whether something is worth money is the thing a customer quotes back at
  you — it can't be a language model's guess. An `only if` with no condition is
  rejected outright, because that's just a hedge wearing a ruling's clothes.
- **RECEIPTS** never invents a price. You type the real figure and the engine is
  told it may use that one and no other — no percentages, no margins, no
  "typically around £X". Asked for a typical tub price a model produces one that
  sounds right and isn't, and a teardown built on a made-up number is worthless.

Neither show ever names a competitor; the target is always how the category
prices itself.

### The posts that matter most

- **D+0, launch day** — `demo_post`: asks three questions in the carousel and
  answers them for the common cases. Useful without ever clicking; the quiz is
  the honest next step, not a pivot.
- **D+2** — the audit that bins more than it keeps, making the point that the
  quiz will tell you to take less. The single best trust-buyer of launch week.
- **D+9** — *"it's just the same stuff I'd buy anyway" — yeah, mostly.* Concedes
  the strongest objection completely, then argues the real value. Objection
  posts are where the conversions live once the novelty is gone.
- **D+19** — the boring FAQ post: what it asks, is it free, do I have to buy
  anything, can I cancel. Least exciting post on the account; will outconvert
  most of them.

---

## Compliance — the one new risk

A personalisation product carries an exposure the rest of the account doesn't:
the gap between a **recommendation** and a **diagnosis**. The quiz reads typed
answers. It does not assess a body, detect anything, or know about a
deficiency. Every framing that crosses that line ("find out what your body is
missing", "see what you're deficient in") would *test brilliantly*, which is
exactly why it can't be left to an editor's judgement at 11pm.

So six patterns went into the claims lint (`chrgd/claims.py`) — deficiency
claims, diagnostic framing, "tailored to your body", prescribing — alongside
the existing EFSA/ASA/CAP rules. They run offline on every post, and
`[campaign.compliance]` tells the writer the same rules up front so the first
draft lands inside the line.

`[campaign.facts]` also holds a **never-say** list: no supplier, no fulfilment
partner, no platform names, nothing about the range being new or the account
being small.

> **No date is a supported state, not a gap.** The one thing the content will
> never do is name a day: "this week", "tomorrow" and any countdown are in the
> `tease` phase's banned list and asserted against in the tests. A date said out
> loud is a promise, and a missed one is the worst first impression a new brand
> can make — "it's nearly ready" costs nothing and can't be broken.

---

## Running it — in the studio

Two journeys in **/create**:

- **⚡ The launch** — sits under the show grid. Opens on the phase you're
  actually in (chips let you move off it to batch next week), shows that
  phase's goal and its permitted ask, then lists the written posts for it —
  tap one and it builds as the right show and format, already stamped. Ones
  you've started are marked. A free-text box under them writes anything else
  for that phase, under the same brief.
- **🧪 THE STACK** — asks the sort first (which one are you / keep or bin /
  stop buying / the demo), subject optional.
- **⚖️ VERDICT** — pick the thing, then hand down *your* ruling: worth it, not
  worth it, or only if (which demands its condition there and then).
- **🧾 RECEIPTS** — the product and its real price. That figure is the only
  number the engine is allowed to use.

Because the phase is stamped on the row rather than read from the clock, a post
written through the launch door on a Tuesday in `prime` still builds under the
launch-week brief. That's how you get launch week made in advance.

## Running it — from the CLI

```bash
chrgd campaign status                    # where we are, and this week's ask
chrgd campaign seed --all --dry-run      # the whole calendar, nothing written
chrgd campaign seed --phase prime        # queue the pre-launch run
chrgd build --count 11                   # write them
chrgd render G-0001                      # images
chrgd export --week                      # Metricool CSV + assets
```

Seeding stamps show, mechanic and phase onto each row, so `build` needs no
further choices. It dedupes on the concept note — re-seed after editing the
calendar and only the new posts queue.

Batching ahead of the calendar works: a row's stamped phase beats today's date,
so you can write launch week during prime and the brief still reads as launch
day.

## Where to put your attention

1. **Set `uses_ai` honestly** in `config/campaign.toml [campaign.tech]`. It is
   `false` by default. If the quiz is a rules-based recommendation flow, calling
   it AI is a false claim about your own product — the easiest kind for a
   sceptic to disprove and the most expensive to walk back.
2. **Leave the date alone until you have one.** Pinned to `buildup` is the
   correct state, and the content it makes is worth posting regardless of when
   you launch.
3. **Paste 3–6 of your best-performing real posts into `brand_bible.md`.** It's
   loaded into every build and it is still the highest-leverage lever in the
   repo — the placeholders are costing you quality on all 29 of these.
4. **Check the D+0 and D+9 posts by hand before they go out.** Everything else
   can run on the gate.

---

## Teasing what's coming, without the exposure

The `buildup` phase is how the account talks about the launch before there is
one. Three rules make it work rather than backfire:

**The post still has to work for a stranger.** Four or five slides of something
genuinely useful, funny or annoying — the kind of post worth making even if we
sold nothing. The tease is the *last slide only*.

**Tease the thinking, not the launch.** "Something amazing is coming" gives a
stranger nothing; they have no relationship with us and no reason to care that
we're excited. What lands is the argument — *nobody can honestly answer "what
should I take" in a comment, so we got annoyed enough to build something that
does*. That earns a follow because the reader already agrees with the post they
just finished.

**Concrete beats excited.** "It asks about six questions and tells you what to
skip" is a better tease than "state of the art", because it's specific enough to
picture and it promises something useful rather than something exciting.

### What's banned, and why it's enforced rather than trusted

- **"State of the art", "cutting-edge", "next-generation", "first of its kind"**
  — objective superiority claims that have to be substantiated on demand under
  UK advertising rules. Three lint patterns catch them.
- **AI / machine learning**, unless `[campaign.tech] uses_ai = true`. Describing
  a rules-based recommendation flow as AI is a false claim about our own
  product.
- **"It works out what your body needs"** — a diagnostic claim. A
  clever-sounding system makes this *worse*, not better, because it sounds like
  it might really know.
- **Any date or countdown.** A date said out loud is a promise; a missed one is
  the worst first impression a new brand can make.
