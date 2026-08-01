# THE MULTIVERSE — roster draft v1

**For your approval.** This becomes `config/roster.toml` once you've cut it.
Companion to `THEMES_PLAN.md` (Show 2, decisions D1/D3/D6).

Each entry carries the four things the engine actually needs:

- **The trait** — the one thing about them that *generates jokes*. This is the
  whole entry. A character without a joke engine is a costume, and they die at
  episode two.
- **Their gym role** — what they're there for, so a storyline has somewhere to
  put them.
- **Visual lock** — the comic-style prompt prefix held identical every render,
  the same trick `chrgd/character.py` uses on Amp.
- **Safety class** — `meme_character` (no rights holder, no constraints beyond
  taste) or `public_figure` (the likeness gate applies in full).

---

## The world

**Iron Palace — Unit 4, a retail park, between a Greggs and a vape shop.**
A 24/7 mid-tier gym that, for reasons nobody in the comic ever explains, is the
one fixed point every reality leaks into. One squat rack short. The lat pulldown
has been "BACK SOON" since 2019. There is a single working hairdryer.

**The engine of the series** — the thing that renews conflict forever: *the
gym is the only place in the multiverse where nobody gets special treatment.*
A god, a Premier League striker and a sentient banana all have to wait for the
same bench. Every episode is a status contest that the room refuses to settle.

**The voice:** comic-book narration boxes (dry, deadpan, third-person) plus
speech bubbles. Never a TikTok voiceover — the narration is the format.

---

## Tier 1 — the regulars (meme characters)

Safest half of the roster and, I think, the funnier half. These carry the show;
public figures guest-star into their storylines.

### 1. Tralalero Tralala · `meme_character`
Three-legged shark in Nike trainers.
- **Trait:** has three legs and *still* skips leg day, and will not discuss it.
- **Gym role:** the cardio guy. Treadmill only. Owns the gym's fastest 5k and mentions it hourly.
- **Visual lock:** blue cartoon shark, upright, three legs in white trainers, bold comic ink, halftone shading.

### 2. Ballerina Cappuccina · `meme_character`
Ballerina with a cappuccino cup for a head.
- **Trait:** is literally made of caffeine. Permanently over-stimulated, cannot self-assess, always doubling the scoop.
- **Gym role:** the pre-workout dealer. Every crash in the building traces back to her.
- **Visual lock:** ballerina in a pink tutu, oversized coffee-cup head with a face, en pointe, comic ink and halftone.

### 3. Bombardiro Crocodilo · `meme_character`
Crocodile-bomber-plane hybrid.
- **Trait:** solves every problem with wildly disproportionate force. Has never once used a lighter weight.
- **Gym role:** ego lifting given a body. The reason the 40kg dumbbells are dented.
- **Visual lock:** green crocodile head and arms on a grey bomber-plane fuselage with wings, comic ink, halftone.

### 4. Tung Tung Tung Sahur · `meme_character`
Wooden figure with a bat.
- **Trait:** turns up at 3am and takes it personally that you didn't.
- **Gym role:** the 5am club, enforced. Runs the sign-in sheet like a court.
- **Visual lock:** tall thin wooden-log character with a carved face and long arms, holding a small bat, comic ink.

### 5. Chimpanzini Bananini · `meme_character`
Monkey in a banana body.
- **Trait:** is the pre-workout snack, is fully aware of it, and has complicated feelings about being eaten.
- **Gym role:** the nutrition running gag. Present at every food conversation, visibly uncomfortable.
- **Visual lock:** small monkey face and limbs emerging from a yellow banana body, comic ink, halftone.

### 6. Shrimp Jesus · `meme_character`
The Facebook AI-slop icon.
- **Trait:** dispenses miracles nobody asked for, then demands engagement for them — "type AMEN for gains".
- **Gym role:** the gym's spiritual authority, and a running joke about engagement farming *inside* a post that is itself asking for engagement. Cheap, and it works.
- **Visual lock:** serene robed figure with shrimp features and shrimp limbs, glowing halo, deliberately over-rendered AI-slop sheen inside a comic frame.

### 7. **Orangina** — `meme_character` · ⚠️ **need you to confirm which one**
You named this one and I don't want to guess wrong — there are a few orange
AI characters circulating (an Orangina-bottle creature, and the orange
brainrot ones). Send me the video or a description and I'll write the entry;
I've held the slot.

---

## Tier 2 — the guest stars (public figures, caricatured)

The likeness gate in `THEMES_PLAN.md` applies to every one of these: caricature
never photoreal, never using or endorsing a product, no fabricated quotes or
news framing, punch at status and situation only. All living public figures.

### 8. Erling Haaland · `public_figure`
- **Trait:** has fully optimised the human body and cannot process fun. Raw liver, unfiltered milk, blue-light goggles, in bed at 8pm.
- **Gym role:** the terrifying benchmark. Everything he does is correct and everyone finds it deeply unsettling.
- **Joke engine:** perfection as a personality disorder.

### 9. Jack Grealish · `public_figure`
- **Trait:** performs at the top level *despite* an all-inclusive lifestyle, and knows exactly how annoying that is.
- **Gym role:** living proof the shortcuts work. The counterargument to every post the account has ever made.
- **Joke engine:** the direct foil to Haaland — put them in one panel and it writes itself.

### 10. Joe Wicks · `public_figure`
- **Trait:** relentless, unbreakable, faintly frightening cheerfulness. Cannot be discouraged by any force known to physics.
- **Gym role:** the volunteer motivator nobody asked for.
- **Joke engine:** infinite positivity meeting a room of cynical British gym-goers at 6pm on a Tuesday.

### 11. Gordon Ramsay · `public_figure`
- **Trait:** cannot look at food without rating it; treats a shaker like a failing restaurant.
- **Gym role:** runs the protein bar. Nobody has ever received a drink without a verdict.
- **Joke engine:** his exact register, aimed at a scoop of whey.

### 12. Jeremy Clarkson · `public_figure`
- **Trait:** believes fitness is a conspiracy invented to sell water bottles. Attends solely for the sauna and the café.
- **Gym role:** the resistance. The one voice in the building on the viewer's side at 6am.
- **Joke engine:** aggressively British anti-wellness — and the best possible foil for Joe Wicks.

### 13. Molly-Mae · `public_figure`
- **Trait:** turns every moment into content and every setback into a lesson. The same 24 hours in a day, deployed relentlessly.
- **Gym role:** the influencer arc, and the show's bridge into the reality-TV territory Live Wire scouts.
- **Joke engine:** hustle-speak applied to a broken leg-press.

---

## The bench (write them in if you want them, cut the ones you don't)

| Who | Trait | Class |
|---|---|---|
| Ronnie Coleman | No weight is ever heavy and no consequence is ever real. "Light weight, baby." | `public_figure` |
| David Goggins | A rest day is a moral failure. Escalation without end. | `public_figure` |
| Arnold Schwarzenegger | Dispenses ancient, mildly unhinged wisdom nobody asked for. | `public_figure` |
| Peter Crouch | Too tall for every piece of equipment, endlessly good-natured about it. | `public_figure` |
| Roy Keane | Judges your form, your kit, your reasons for being alive. | `public_figure` |
| Brr Brr Patapim | Speaks only in nonsense and is somehow always right. | `meme_character` |

**Deliberately left off**, and I'd keep them off: anyone with live legal
proceedings or allegations attached, anyone famous *for* a supplement scandal
(too close to our own category to be a joke), and the manosphere fitness
figures — the reach they'd bring is not the audience you want commenting.

---

## How a season works

The roster is permanent; the **format** is the season. Six to eight episodes,
then the world resets into a new one with the same cast.

1. **Season 1 — "The Villa."** A reality dating format at Iron Palace. Couplings, recouplings, one squat rack, a public vote.
2. **Season 2 — "The Tournament."** A Hyrox-style knockout. Bracket format, one elimination per episode — the cleanest possible engine for a serial.
3. **Season 3 — "The Management."** A mockumentary: one of them buys Iron Palace and institutes policy. (Your Hyrox-boss idea, recast.)

**Cast size per episode: 2–4.** More than four and a carousel can't hold them —
this is the single most common way a comic serial goes soggy.

---

## What I need from you

1. **Cuts.** Which of the 13 don't earn their slot?
2. **Orangina** — which character do you actually mean?
3. **Anyone missing** who your audience would clock instantly?
4. Season 1 = the Villa as planned, or open on the Tournament? **→ default: the Villa, it's the one you started from and the couplings generate more story than a bracket.**
