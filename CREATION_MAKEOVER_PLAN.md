# CREATION MAKEOVER PLAN — the next big leap

**Scope:** the whole creation journey, app open → the moment a build kicks off. Not incremental polish — the significant swings worth taking next. **Plan only; nothing here is implemented.**

**Where we are:** the two prior audits shipped and deployed — slide-1 concept gate, native aesthetic, honest QA, engagement-signal delivery, pillars, a learning loop, rewritten ragebait/trending prompts, two-worker split, two-stage scans, mobile-first source screen. The engine is sound. This plan is about making the *content it decides to make* sharper, more UK-native, and more magnetic — and turning "what do I make today" from a menu into a strategist.

The single biggest lever left: **the studio still waits for you to choose, then makes one thing.** The leap is to make it *propose today's winning post, UK-first, cast to convert* — and to give you more to *choose* from at the point that decides reach (slide 1), without spending a penny more.

### Hard constraints (locked with the operator)
- **One post a day.** No week-planner, no 5-8 slate — the daily experience is "here's today's strong pick," singular.
- **No image-spend increase.** One image per slide, one slide-1 image, exactly as today. Every idea below that adds value does it at the **concept/text layer** (cheap LLM calls), never by rendering extra images or pre-rendering posts.
- **UK-only.** Build for Britain now; don't build a multi-market abstraction — expansion is a "cross it later" bridge, not a thing to engineer today.
- **Casting = one locked face + fresh cast.** Exactly one real recurring person (their actual face, pixel-locked as today); everyone else is freshly cast and aspirational, identity irrelevant.

---

## The big bets (prioritised by impact)

### Bet 1 — The Casting System (the attractiveness lever, done properly)
**The lever:** attractive, aspirational subjects measurably lift stop-rate and watch-through on fitness content. This is a real, ownable dial — today the system has *one* optional recurring character and a per-slide "is a person in this shot" boolean (`images.slide_features_character`, `profile.character/character_image`). There is no concept of *who* the cast is, how attractive/aspirational they read, or how to tune it.

**What to build (per the locked casting decision — one real face + fresh cast):**
- **Keep the one locked person; describe the rest.** The existing `character_image` pixel-lock stays as-is for the single real recurring person (their actual face). Add a **described "supporting cast" direction** in the brand profile — aspirational UK gym people, fresh each post, identity irrelevant — that the engine casts freely into any beat that isn't the locked person. No extra locked portraits to manage.
- **An attractiveness dial** (`casting_intensity`: `off / natural / elevated`) injected into image prompts, so you set the level once and it threads through every render via a new casting block (same plumbing as `profile_style_block` / `_design_system_block`). No extra image cost — it only changes the prompt on the image we already generate.
- **Per-beat casting, staying native.** Extend the engine's per-slide `feature_character` call to also decide: the locked person, or a fresh supporting-cast member — with a *flattering-but-candid* framing (an attractive person in a real UK gym in fitted athletic wear, phone-shot, never a glamour/catalogue shot, which reads as an ad and gets scrolled). "Aspirational + native" is the target, and it's also the *highest-performing* one.
- **Learn what actually converts.** Track casting level as a learning trait (like pillars/engagement_play), so the loop tells you whether `elevated` actually beats `natural` for *saves/shares* on your audience — instead of guessing. Often "subtle + relatable" out-saves "glossy"; let the data settle it. (Text/label only — no spend.)

**Guardrails — baked in, and they're performance guardrails not just safety ones:**
- **Adults only, unambiguously.** All subjects read clearly adult; the prompt says so and a validator blocks anything otherwise.
- **Stay the right side of the platform + the image API.** TikTok actively *suppresses* overtly sexual content (shadow-bans, "not eligible for FYP") and gpt-image will *refuse* over-sexualised prompts (failed renders, wasted spend). So "tasteful, aspirational, gym-appropriate" isn't the timid option — it's the one that actually reaches the FYP and renders at all. Crude = less reach, not more. Your own instinct ("not over the top, just there") is exactly right and is also the optimum.
- **Brand trust with real buyers.** A supplement brand's buyers are largely people who train; casting that's aspirational earns them, casting that's leery loses them. The dial lets you find that line and the learning loop verifies it.

**Where:** `profile.py` (supporting-cast direction + dial), `pipeline.py` build contract (per-slide cast choice), `images.py` (`compose_design_prompt` casting block), `learning.py` (casting trait), settings UI. **Size: M-L.** **Risk:** medium — needs the guardrail validator and a taste pass; entirely additive to the existing character system, zero extra image spend.

### Bet 2 — UK-first market spine
**The lever:** "UK gym" is sprinkled through prompts as words, but nothing *enforces* a market, and the moments radar rediscovers British context from scratch every scan. UK-native specificity (the £4.50 meal deal, the 22:47 kick-off, PureGym at 6pm, the January rush) is what makes a stranger feel "this is *my* world" — the deepest stop trigger.

**What to build (UK-only — no multi-market abstraction):**
- **A single UK context pack** threaded into every discovery prompt, every take, and the build: named gym chains (PureGym, The Gym Group, JD, Gymshark culture), £ pricing, GMT/BST times, UK spelling, the light-Northern voice already defined. Hardcode it as *the* market — a `config/uk_context.toml` the prompts pull from — rather than a configurable market system (that's the later bridge, not now).
- **A persistent UK cultural calendar** — a small curated, editable knowledge file (bank holidays, payday Fridays, England/Six Nations/big fixtures, Love Island/telly seasons, weather turns, New-Year rush) that seeds the moments lane *before* web search, so it always knows what week it is in Britain instead of paying a scan to find out. (Cheaper, not more expensive — it can *reduce* scan reliance.)
- **UK-native casting + aesthetics** fold into Bet 1 (British gym aesthetics, not American).

**Where:** new `config/uk_context.toml`; threaded into `trends.py` prompts, `pipeline.engine_base`, `profile.py`. **Size: M.** **Risk:** low — additive, prompt/context only, no spend.

### Bet 3 — "Today's pick" (right-sized for one post a day)
**The lever:** at one post a day, you don't need a week-planner — you need the app to open on *one strong, ready recommendation* instead of a blank door. Warm-on-open already pre-scans the lanes cheaply; this just picks the best of what's already there.
**What to build:** a **"Today's pick"** card on the create screen — from the already-warmed lane scans + your pillars/engagement history, surface the single strongest UK concept for today (headline + slide-1 concept + suggested cast member), one tap to build. It's **concept-level only** — no pre-rendered posts, no extra image spend; it reuses scans that already ran. A "show me a couple more" reveals 2-3 alternates (also concept-level). **Size: M.** **Risk:** low — text/selection only, no new spend.

### Bet 4 — Slide-1 concept options (choose the opener — no extra renders)
**The lever:** slide 1 is ~90% of reach, yet you currently pick from 3 hook *lines* only, then the engine commits the visual. Per the locked decision — **more concepts to choose from, but only one image rendered** — give the human the full slide-1 *concept* to pick, not just the words: for each option, the hook **and** its visual direction **and** which cast member. You pick the opener you believe in; then the single render happens on the chosen one. All exploration is text/concept (cheap LLM); the image count is unchanged. **Where:** extend the build contract's `hook_options` into richer `slide1_options` (hook + visual brief + cast); a picker in the create UI before render; the concept gate still scores the chosen one. **Size: M.** **Risk:** low — no image-spend change; it's the existing hook picker, deepened.

### Bet 5 — Winner remix + reference ingestion
**The lever:** the learning loop knows your hits but nothing *reuses* them. **What to build:** "Make another like this" on any logged winner → sequels/variations on the shape that worked; and a **reference door** — paste a competitor's viral post (or describe it) and get "our UK version, our cast, our angle." Doubling down on breakouts is exactly what the rubric asks for. **Size: M.**

### Bet 6 — Living hook bank
**The lever:** `brand_bible.md` is a static, hand-edited exemplar file. Make it **living and performance-ranked** — the account's best-performing hooks/openers accumulate automatically from logged results and seed the takes/angles fan-out as few-shot exemplars, so every new idea stands on your proven winners. **Size: M.**

---

## Suggested phasing

1. **Bet 2 (UK spine)** — foundational, low-risk, no spend; makes everything else more native. Do first.
2. **Bet 1 (Casting)** — the headline lever; needs the guardrail + taste pass, so give it room.
3. **Bet 4 (Slide-1 concept options)** — deepen the existing hook picker; no spend change.
4. **Bet 3 (Today's pick)** — the UX leap, right-sized for one post a day.
5. **Bets 5 & 6** — compounding wins once there's logged history to remix and rank.

## Decisions (locked with the operator)
- **Casting:** a **mix**; exactly **one** real recurring person (their actual face, pixel-locked as today), everyone else **freshly cast** and aspirational, identity irrelevant. Intensity dial ships with a sensible default (`natural`), tunable in settings.
- **Market:** **UK-only.** Hardcode the UK context now; no multi-market abstraction — expansion is a later bridge.
- **Spend:** **no increase.** One image per slide, one slide-1 image, as today. Every value-add here is at the concept/text layer or reuses scans that already run; **no bracket renders, no pre-rendered slates.**
- **Cadence:** **one post a day** — the daily experience is "today's pick," singular, not a week planner.

Next step: turn this into a concrete, phased CHANGE_PLAN with the actual prompts, schema and diffs — same as the last two rounds — starting with Bet 2 (UK spine). Say the word to build.
