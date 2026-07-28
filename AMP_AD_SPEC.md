# Amp — 10s end-of-video AI ad · Requirements

The spec for the 10-second AI-generated ad that runs at the end of getCHRGD
fitness videos. Amp is the star; the ad drives to **the quiz** or **the bundle
walked through in the video**, each buyable as a one-off or on the monthly
subscription with deliveries at the right cadence.

> **Source note:** no reference images were attached to the session this spec was
> written in. Amp's definition below is taken from the canonical description
> already in the codebase (`chrgd/character.py`), not from the pics. Before
> generating, check the spec's character block against your reference images and
> correct anything that has drifted — `character.py` is the thing to update if
> they disagree, so the carousels and the ad stay the same character.

---

## 1. Character definition (locked)

Amp already exists as code. The ad must not re-invent him — it reuses the same
locked description so the ad and the carousels are visibly one character.

**Canon:** `chrgd/character.py` → `CANONICAL_PROMPT`, `STYLE_LOCK`,
`CHARGE_STATES`, `PERSONA`, `SIGN_OFF`.

| Attribute | Value | Source |
|---|---|---|
| Name | Amp, the getCHRGD mascot | `NAME` |
| Form | Friendly cartoon character shaped like a bold, rounded lightning bolt | `CANONICAL_PROMPT` |
| Body colour | Electric cyan-blue **#29C2F2**, thick clean black outline | `CANONICAL_PROMPT` |
| Face | Two round white eyes, small black pupils, small expressive mouth | `CANONICAL_PROMPT` |
| Limbs | Short stubby arms and legs | `CANONICAL_PROMPT` |
| Proportions | Head-heavy, ~2.5 heads tall | `CANONICAL_PROMPT` |
| Render style | Chunky sticker-style flat vector, minimal shading, bold outlines, **no photoreal** | `STYLE_LOCK` |
| Personality | Lovable try-hard on the journey *with* the audience, not a superhero. Dry British humour. Never corporate. | `PERSONA` |
| Sign-off | "Stay amped." | `SIGN_OFF` |

### Charge states — the ad's emotional spine

Amp's charge level *is* the narrative. The ad runs one compressed charge cycle:

| State | Charge | Body | Posture | Used at |
|---|---|---|---|---|
| `drained` | ≤30% | Desaturated grey-blue, no glow, visibly dim | Slouched, droopy eyes, red low-battery tint at his feet | Shot 1 (0.0–1.2s) |
| `charging` | 31–79% | Normal cyan, mild glow, a few sparks | Upright, straightening out | Shots 2–5 (door scenes) |
| `charged` | ≥80% | Vivid cyan, bright glow and aura, lightning arcs | Confident hero pose, big grin | Shots 6–7 (CTA) |

Reuse `charge_block(charge)` to generate the per-shot state text rather than
writing it by hand — it keeps the wording identical to the carousels.

### Video-specific additions (not yet in `character.py`)

Stills don't need these; video does. **These need adding to the canon before the
ad is generated, or every clip will drift:**

- **Walk cycle** — bolt-shaped body means no hip rotation. Specify a bouncy,
  slightly waddling two-frame-feel walk; body stays rigid, arms swing wide.
- **Idle/breathing** — a subtle pulse in the glow, synced roughly to 1Hz. The
  glow pulse is the "alive" signal and should scale with charge state.
- **Turnaround** — flat vector characters break when they rotate. Lock Amp to
  **3/4 front, front, and profile only.** Explicitly negative-prompt rear views
  and any rotation through the thin edge of the bolt.
- **Scale reference** — Amp is roughly the height of the delivery box (~40cm) so
  he reads as a mascot in a real doorway, not a person. Fixed across all shots.
- **Shadow** — a soft contact shadow plus a faint cyan light-spill onto the floor
  near him. This is what stops a flat vector character looking pasted on.

---

## 2. Format and delivery specs

| Spec | Value | Rationale |
|---|---|---|
| Duration | 10.0s hard | Runs as a tail card; longer gets cut by the host video's end |
| Aspect | 9:16, **1080×1920** | ⚠️ Differs from the carousel canvas (1080×1620, 2:3, `brand.toml`) — do not reuse the carousel size |
| Frame rate | 30fps (300 frames) | |
| Codec | H.264 MP4, ~8–10 Mbps | |
| Audio | Stereo 48kHz, −14 LUFS | |
| Safe zones | top 10%, bottom 24%, left 8%, right 18% | Reuse `brand.toml [safe_zones]`; keeps CTA clear of TikTok UI |
| Sound-off legibility | Every claim must be readable as on-screen text | Majority of feed views are muted |
| Endcard hold | Final frame held ≥1.5s | Gives the tap target time to land |

### Palette

| Role | Hex | Note |
|---|---|---|
| Amp's body | `#29C2F2` | `character.py` |
| Brand accent / UI, type, CTA | `#4FC3F7` | `brand.toml [colors].accent` |
| Background base | `#0B0B0D` | `brand.toml` near-black |
| Headline | `#FFFFFF` | |
| Accent alt | `#8FD9F7` | Glow falloff, secondary type |

> ⚠️ **Colour conflict to resolve.** `character.py` puts Amp's body at `#29C2F2`
> and `brand.toml` puts the brand accent at `#4FC3F7`. They're close enough to
> look like a mistake on screen when both appear in the same frame. Decision
> needed: either align them, or deliberately keep Amp slightly deeper than the UI
> so he separates from the type. Recommendation: **keep both as-is** — the
> separation helps Amp pop against accent-coloured captions — but write it down
> as intentional so it isn't "fixed" later.

---

## 3. Shot list — 10 seconds

Seven shots. The four door scenes are the middle block and carry the cadence
story: **same door, four deliveries, time visibly passing.**

| # | Time | Dur | Content | Amp charge |
|---|---|---|---|---|
| 1 | 0.0–1.2 | 1.2s | **The drain.** Amp slumped, grey-blue, low-battery tint. Post-session flatness. | 10% `drained` |
| 2 | 1.2–2.9 | 1.7s | **Door 1 — first delivery.** Bright morning. Box on the step, Amp perks up. | 40% `charging` |
| 3 | 2.9–4.4 | 1.5s | **Door 2 — the rhythm starts.** Overcast midday, autumn leaves. Box lands. | 55% `charging` |
| 4 | 4.4–5.9 | 1.5s | **Door 3 — it's just normal now.** Rain on the step, porch light on. Box. | 70% `charging` |
| 5 | 5.9–7.2 | 1.3s | **Door 4 — locked in.** Frosty evening, warm light from inside. Box. | 79% `charging` |
| 6 | 7.2–8.6 | 1.4s | **The payoff.** Amp fully charged, hero pose, arcs and sparks, bundle beside him. | 100% `charged` |
| 7 | 8.6–10.0 | 1.4s | **Endcard.** Two CTAs + "Stay amped." Held to the last frame. | 100% `charged` |

### The four door scenes — consistency rules

This is the hardest part of the ad and where AI video usually falls over. The
four shots must read as *the same house, four different times*.

**Held identical across all four (the "plate"):**
- The door itself — colour, panel layout, letterbox, handle, house number.
- Camera: locked-off, no move, eye-level-ish at ~1.2m, same focal length and
  framing every time. **No camera motion in any door shot** — the stillness is
  what sells "same door".
- The step, the doormat, the frame, the wall material.
- The delivery box: same size, same getCHRGD branding, same placement mark on
  the step.

**Changed deliberately, to show time passing:**
- Light and weather: bright morning → overcast autumn → rain → frosty evening.
- Small set dressing: leaves, a puddle, frost on the step edge.
- Amp's charge state and pose.

**Production method (important).** Do not generate four door scenes
independently — they will not match. Instead:

1. Generate **one** door still at the locked framing and approve it.
2. Use that approved frame as the **first-frame / image-to-video reference** for
   all four clips, changing only the lighting and dressing in the prompt.
3. If the generator supports it, also pass it as a style/identity reference.

This is the same trick `character.py` already documents for Amp himself: the
module notes that text alone drifts and that the strongest setup is one approved
canonical render saved as `BrandProfile.character_image`. **Do that for Amp
before generating any shot, and do the equivalent for the door plate.** Two
locked reference images — Amp, and the door — are the single highest-leverage
thing you can do for this ad.

---

## 4. Script

Total spoken ≈ 22 words — about the ceiling for 10s at a natural pace.

| # | On-screen text | VO / caption |
|---|---|---|
| 1 | *(none — let the visual land)* | "Training's the easy bit." |
| 2 | **One-off or monthly** | "Get the stack sent to your door…" |
| 3 | | "…at the cadence you actually drink it." |
| 4 | **Free delivery over £50** | |
| 5 | **Save up to 25% on subscription** | "Subscribe and save." |
| 6 | **[Bundle name]** | "Or take the 60-second quiz." |
| 7 | **Take the quiz · Buy the bundle** — *Stay amped.* | "Stay amped." |

**Voice:** dry, UK, light Northern — per `brand_bible.md`. Not a hype-man, not a
brand voice doing "fun". Banned words from the brand bible apply to ad copy too:
*optimise, unlock, level up, crush your goals, game-changing, elite mindset*.

**Bundle name is a variable.** The ad is per-video — shot 6 shows whichever
bundle that video walked through. Current bundles (`quiz/src/lib/bundles/`):

| Slug | Name | Tagline |
|---|---|---|
| `big-night-big-morning` | Big Night, Big Morning | Hydrate. Move. Refuel. Reset. |
| `deadline-week` | Deadline Week | Keep the routine. Ride out the week. |
| `early-shift` | Early Shift | Up. Out. Done before work. |
| `game-day` | Game Day | Hydrated. Sharp. Ready for kick-off. |
| `leg-day-loading` | Leg Day Loading | Fuel it. Move it. Refuel it. |
| `wind-down-sunday` | Wind-Down Sunday | Move gently. Reset properly. Start Monday ahead. |

Shot 6 and shot 7 should therefore be generated **per bundle** (6 variants) while
shots 1–5 are generated once and reused. That's 5 shared clips + 12 variant
clips, not 42.

---

## 5. Commercial facts — verified against the code

Every number the ad can state, with its source. **Do not put a figure on screen
that isn't on this list.**

Source: `quiz/src/lib/stack-blueprint/pricing.ts` → `PRICING_CONFIG`.

| Claim | Value | Field |
|---|---|---|
| Subscribe & save — Essentials | 15% | `levelSubscriptionDiscount.essentials` |
| Subscribe & save — Performance | 20% | `levelSubscriptionDiscount.performance` |
| Subscribe & save — Complete | 25% | `levelSubscriptionDiscount.complete` |
| One-off bundle discount | 10% / 15% / 20% at £50+ / £90+ / £120+ | `bundleTiers` |
| Free delivery | Orders £50+ | `freeDeliveryThreshold` |
| Plan name | "CHRGD Monthly Stack Plan" | `subscriptionPlanLabel` |
| Billing | One flat amount monthly (smoothed average) | `subscriptionFlatMonthly` |
| Minimum subscription value | £25/month | `minSubscriptionMonthly` |
| **Minimum term** | **4 months** | `minSubscriptionMonths` |
| Max gap between deliveries | 3 months | `maxDeliveryMonths` |
| Intro offer | 25% or 50% off first month, scratch-to-reveal | `introOffer.scratchReveal` |

**Safest headline claim for shot 5:** "Save up to 25% on subscription" — true at
the Complete level, and "up to" covers the other tiers.

### The cadence story is real, and it's the ad's best asset

The delivery rhythm isn't marketing dressing — it's how the product actually
works. Products carry a `ConsumptionCadence` (`quiz/src/lib/catalogue/types.ts`):

- `daily` — protein, creatine, multivitamin
- `per-workout` — pre-workout, intra/EAA
- `as-needed` — electrolytes, sleep

The subscription sizes each product to its own cadence, bills one flat monthly
amount, and ships when each one actually runs out (never more than 3 months
apart). **That's exactly what the four door scenes are showing.** Shot 3's
caption should carry it: *"at the cadence you actually drink it."*

---

## 6. Compliance — three things that will get the ad pulled

These are real problems with the ad as briefed, not hypotheticals. Flagging them
now because they're cheap to fix in the script and expensive to fix after a
platform rejection or an ASA complaint.

1. **The 4-month minimum term must be disclosed.** `minSubscriptionMonths: 4`
   means "cancel anytime" is **false** and must not appear in any form. The ad
   pitches the subscription as the money-saving option, so the tie-in is material
   information. Fix: a persistent small-print line on shots 5–7 — *"Min. 4-month
   term. T&Cs apply."* It costs nothing and it's the difference between a
   compliant ad and a pulled one.

2. **Don't advertise "50% off your first month" as a flat claim.** The intro
   offer is a weighted random draw — 25% off with weight 2, 50% off with weight
   1, so **two thirds of people get 25%**. Advertising the 50% outcome as the
   offer is misleading. Fix: *"25–50% off your first month"* or *"up to 50% off
   your first month"*, and only if the scratch mechanic is explained at the
   landing page.

3. **"Save up to 25%" needs its basis on screen.** The 25% is the Complete-level
   subscription rate, not a universal one. The "up to" does most of the work, but
   pair it with the small print so the basis is available.

Also worth a look before publishing: platform rules on supplement advertising
vary (some restrict targeting and health claims). Keep the ad to *convenience and
price* — delivery, cadence, saving — and make **no health, performance, or body
composition claims**. Nothing in the current script makes one; keep it that way.

---

## 7. Engine gaps — what the studio can't do yet

This repo is an **image carousel engine**, not a video engine. Concretely:

- `brand.toml [generation]` targets gpt-image-2 stills at 1024×1536; there's no
  video generation path, no timeline, no audio, no stitching.
- `render_mode = "ai_design"` bakes text into stills via the image API. For video
  this is the wrong approach — **captions and the endcard must be composited in
  post**, not generated per-frame, or the type will crawl and warp between
  frames. This is a genuine reversal of the house rule, and it's correct: the
  no-overlay rule exists for stills and doesn't survive contact with motion.
- `chrgd/character.py` has no motion vocabulary (see §1 additions needed).

**What's needed to produce this ad:**

| Need | Detail |
|---|---|
| Approved Amp reference render | One canonical still, saved as `BrandProfile.character_image`. Blocks everything else. |
| Approved door plate | One locked-framing door still, reused as first frame for all 4 door clips. |
| Video generation | External (Veo / Kling / Runway). Most produce 4–8s max, so 10s **must** be stitched from clips regardless. |
| Compositing | Captions, endcard, small print, logo. After Effects / Resolve / ffmpeg. |
| Audio | VO + a music bed, ducked under VO, −14 LUFS. |
| Motion canon | Extend `character.py` with the walk/idle/turnaround/shadow rules. |

**Recommended build order:** Amp reference render → door plate → 4 door clips →
shots 1/6/7 → composite → per-bundle variants of 6 and 7.

Doing it in that order means the two expensive-to-redo decisions (what Amp looks
like in motion, what the door looks like) are locked before any clip spend.

---

## 8. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Which CTA leads — quiz or bundle? | **Bundle.** The viewer just watched it being walked through; quiz is the fallback for the undecided. Split the endcard, bundle on top. |
| 2 | Amp's body colour vs brand accent (§2) | Keep both, document as intentional. |
| 3 | Is the quiz "60 seconds"? | Unverified — I took it from the script draft, not the code. Count the real question flow before it goes on screen. |
| 4 | Does Amp speak, or is the VO the CHRGD narrator? | **Narrator.** `brand_bible.md` defines the narrator as one specific UK lifter; giving Amp a voice is a new character decision and a lip-sync problem in flat vector. |
| 5 | 6 bundle variants at launch, or 1–2 to test? | Start with the 2 best-performing bundles. Shots 1–5 are shared, so adding variants later is cheap. |
