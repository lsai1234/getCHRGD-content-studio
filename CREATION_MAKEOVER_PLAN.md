# CREATION MAKEOVER PLAN — the next big leap

**Scope:** the whole creation journey, app open → the moment a build kicks off. Not incremental polish — the significant swings worth taking next. **Plan only; nothing here is implemented.**

**Where we are:** the two prior audits shipped and deployed — slide-1 concept gate, native aesthetic, honest QA, engagement-signal delivery, pillars, a learning loop, rewritten ragebait/trending prompts, two-worker split, two-stage scans, mobile-first source screen. The engine is sound. This plan is about making the *content it decides to make* sharper, more UK-native, and more magnetic — and turning "what do I make today" from a menu into a strategist.

The single biggest lever left: **the studio still waits for you to choose, then makes one thing.** The leap is to make it *propose a winning slate for the week, UK-first, cast to convert* — and to spend the exploration budget where reach is actually decided (slide 1).

---

## The big bets (prioritised by impact)

### Bet 1 — The Casting System (the attractiveness lever, done properly)
**The lever:** attractive, aspirational subjects measurably lift stop-rate and watch-through on fitness content. This is a real, ownable dial — today the system has *one* optional recurring character and a per-slide "is a person in this shot" boolean (`images.slide_features_character`, `profile.character/character_image`). There is no concept of *who* the cast is, how attractive/aspirational they read, or how to tune it.

**What to build:**
- **A described cast, not one mascot.** New brand-profile block: a small roster (e.g. an aspirational UK gym woman 22-28, an attractive lad, a relatable everyman) each optionally pinned by a locked portrait (the `character_image` mechanism already does pixel-locking; generalise it to N faces). The engine picks *which* cast member fits each post's angle.
- **An attractiveness dial** (`casting_intensity`: `off / natural / elevated`) injected into image prompts, so you set the level once and it threads through every render via a new casting block (same plumbing as `profile_style_block` / `_design_system_block`).
- **Per-beat casting, staying native.** Extend the engine's per-slide `feature_character` call to also choose the cast member, the pose, and a *flattering-but-candid* framing — an attractive person in a real gym in fitted athletic wear shot on a phone, never a glamour/catalogue shot (which reads as an ad and gets scrolled). "Aspirational + native" is the target, and it's also the *highest-performing* target.
- **Learn what actually converts.** Track casting level + which cast member as a learning trait (like pillars/engagement_play), so the loop tells you whether `elevated` actually beats `natural` for *saves/shares* on your audience — instead of guessing. Often "subtle and relatable" out-saves "glossy"; let the data settle it.

**Guardrails — baked in, and they're performance guardrails not just safety ones:**
- **Adults only, unambiguously.** All subjects read clearly adult; the prompt says so and a validator blocks anything otherwise.
- **Stay the right side of the platform + the image API.** TikTok actively *suppresses* overtly sexual content (shadow-bans, "not eligible for FYP") and gpt-image will *refuse* over-sexualised prompts (failed renders, wasted spend). So "tasteful, aspirational, gym-appropriate" isn't the timid option — it's the one that actually reaches the FYP and renders at all. Crude = less reach, not more. Your own instinct ("not over the top, just there") is exactly right and is also the optimum.
- **Brand trust with real buyers.** A supplement brand's buyers are largely people who train; casting that's aspirational earns them, casting that's leery loses them. The dial lets you find that line and the learning loop verifies it.

**Where:** `profile.py` (cast roster + dial), `pipeline.py` build contract (per-slide cast choice), `images.py` (`compose_design_prompt` casting block), `learning.py` (casting trait), settings UI. **Size: L.** **Risk:** medium — needs the guardrail validator and a taste pass; entirely additive to the existing character system.

**Decisions I need from you:** default level (I'd suggest `natural`)? Cast make-up (female lead only, or a mix)? Do you want locked AI-generated recurring faces (we generate the portraits once) or fresh casting each post?

### Bet 2 — UK-first market spine
**The lever:** "UK gym" is sprinkled through prompts as words, but nothing *enforces* a market, and the moments radar rediscovers British context from scratch every scan. UK-native specificity (the £4.50 meal deal, the 22:47 kick-off, PureGym at 6pm, the January rush) is what makes a stranger feel "this is *my* world" — the deepest stop trigger.

**What to build:**
- **A central `market` setting (default UK)** that threads one context pack into every discovery prompt, every take, and the build: named gym chains (PureGym, The Gym Group, JD, Gymshark culture), £ pricing, GMT/BST times, UK spelling, the light-Northern voice already defined.
- **A persistent UK cultural calendar** — a small curated, editable knowledge file (bank holidays, payday Fridays, England/Six Nations/big fixtures, Love Island/telly seasons, weather turns, New-Year rush) that seeds the moments lane *before* web search, so it always knows what week it is in Britain instead of paying a scan to find out.
- **UK-native casting + settings** fold into Bet 1 (British gym aesthetics, not American).

**Where:** new `config/market_uk.toml` context pack + `Settings.market`; threaded into `trends.py` prompts, `pipeline.engine_base`, `profile.py`. **Size: M.** **Risk:** low — additive, mostly prompt/context.

### Bet 3 — From "what do I make today" to a daily UK slate
**The lever:** the rubric's biggest structural gap is volume + consistency (3-5/week, each a test). The app still makes you choose a door and build one thing. Warm-on-open already pre-scans; go the whole way.
**What to build:** a **"Today's slate"** — a morning cron (the systemd timer already exists) pre-generates 5-8 ready-to-pick concepts (headline + slide-1 concept + suggested cast), balanced across your pillars and engagement plays, UK-timed. You open the app to a *board of today's best bets*, tap one, it's already halfway built. Plus **"Plan my week"** — one action lays a balanced week onto the calendar. **Size: L.** **Risk:** medium (spend pacing on pre-generation).

### Bet 4 — The Slide-1 Bracket (thumbnail studio)
**The lever:** slide 1 is ~90% of reach, yet the flow commits to a full post then judges slide 1 once (the concept gate). Invert it: **explore slide 1 first.** Generate 3-4 competing slide-1 concepts (hook × visual × cast), render them cheap, run the scroll test head-to-head, you pick the winner, *then* build the rest of the carousel around the proven opener. Spend the exploration budget where reach is decided. **Where:** a new pre-build stage before `run_pipeline_for_idea`, reusing the concept gate + scroll test. **Size: L.** **Risk:** medium (more slide-1 image spend up front — but it's the correct place to spend).

### Bet 5 — Winner remix + reference ingestion
**The lever:** the learning loop knows your hits but nothing *reuses* them. **What to build:** "Make another like this" on any logged winner → sequels/variations on the shape that worked; and a **reference door** — paste a competitor's viral post (or describe it) and get "our UK version, our cast, our angle." Doubling down on breakouts is exactly what the rubric asks for. **Size: M.**

### Bet 6 — Living hook bank
**The lever:** `brand_bible.md` is a static, hand-edited exemplar file. Make it **living and performance-ranked** — the account's best-performing hooks/openers accumulate automatically from logged results and seed the takes/angles fan-out as few-shot exemplars, so every new idea stands on your proven winners. **Size: M.**

---

## Suggested phasing

1. **Bet 2 (UK spine)** — foundational, low-risk, makes everything else more native. Do first.
2. **Bet 1 (Casting)** — the headline lever; needs the guardrail + taste pass, so give it room.
3. **Bet 4 (Slide-1 bracket)** — highest reach-per-effort once casting exists to cast the brackets.
4. **Bet 3 (Daily slate)** — the UX leap; best once 1+2 make the pre-generated concepts genuinely good.
5. **Bets 5 & 6** — compounding wins once there's logged history to remix and rank.

## Open decisions for you (so I can size Phase 1 properly)
- **Casting:** default intensity? Cast composition (female lead / mix)? Locked AI faces vs fresh each time?
- **Market:** UK-only, or build the `market` setting to leave room for expansion later?
- **Spend appetite:** OK to increase up-front image spend for the slide-1 bracket and daily-slate pre-generation? Rough monthly ceiling?
- **Cadence target:** posts/week you actually want to ship (drives the slate size)?

Answer those and I'll turn this into a concrete, phased CHANGE_PLAN with the actual prompts, schema and diffs — same as the last two rounds.
