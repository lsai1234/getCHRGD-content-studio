# REVIEW — TikTok Carousel Pipeline Audit

**Scope:** photo carousels only. Every claim below cites the file/function it comes from.

## Executive summary

The single biggest reason this account is capped at ~300 views is not the words — the copy engine is genuinely strong on hooks, arcs and psychology — it's that **every finished slide ships as a polished, AI-designed branded graphic, and nothing independent ever checks it before it posts.** The image layer is explicitly briefed to produce "scroll-stopping social media art… agency-level concept art" (`pipeline.py:59`), wrapped in a "LOCKED BRAND LOOK — IDENTICAL on every slide of every post" (`profile.py:154-172`), with a mandatory progress-dot indicator stamped on every frame (`images.py:336-341`). To a stranger in the first test pool that reads as *an ad made by AI*, and the scroll is instant — no completion, no saves, no escape from the pool. Compounding it: slide 1 — the whole ballgame — gets **one** generation at **medium** quality by default (`brand.toml:19-22`), the internal QA gate is neutered by a buried instruction telling the model to "only emit a post you would pass" (`pipeline.py:97-99`), and the honest adversarial judge that *does* exist (the cold scroll test, `scrolltest.py`) is opt-in, slide-1-only, and its verdict is never persisted or acted on. Finally, the engagement furniture the engine correctly generates is partly thrown away at the door: the `comment_trigger` never reaches TikTok through either posting path (`publisher.py:93-105`, `webapp.py:1372-1403`).

**Top 3 changes by expected impact:**
1. **Slide-1 stop package** — 3 high-quality slide-1 variants, auto-run the cold scroll test after every render, persist the verdict, and regenerate on "scroll". (Levers: thumbnail/scroll-stop, completion.)
2. **Native-aesthetic shift** — replace "agency-level concept art" and the mandatory brand-poster treatment with native TikTok visual formats (phone photo, screenshot, meme, photo-dump) as the default; make the brand lock a palette/character accent, not a poster template. (Levers: native > polished, completion.)
3. **Deliver the engagement signals you already generate** — pipe `comment_trigger` out as a pinned first comment in both export paths, require an explicit save/share CTA per post, and un-neuter the QA gate. (Levers: saves/shares/comments.)

---

## Phase 1 — The map

### Lifecycle end to end

```
IDEAS IN                          CONCEPT                        WRITE                       REVIEW                RENDER                    OUT
─────────                         ───────                        ─────                       ──────                ──────                    ───
capture (capture.py, no LLM)      takes fan-out                  build_one / build           /review UI +          render_carousel           Metricool CSV
trends scout (trends.py)     →    (pipeline.generate_takes)  →   (pipeline.run_pipeline_ →   QA gate          →    (images.py,          →    (publisher.py)
4 discovery lanes                 concept dev                    for_idea, 6-stage           (models.qa_           gpt-image-2,              or manual post
(moments/evergreen/               (develop_concept)              prompt, JSON contract)      failures)             ai_design mode)           (/api/…/manual)
trending/ragebait)                angles (facts door)            revise (revise_post)        scroll test           per-slide regen +
meta scan (scan_meta)                                                                        (opt-in)              variants
```

All LLM work runs through one background worker (`worker.py`), jobs table in SQLite (`db.py`). The web app (`webapp.py`) is the driver; the CLI (`cli.py`) and `orchestrate.run_chain` wrap the same engine.

### Stage-by-stage: who decides what

| Stage | File / function | Prompt source | Hardcoded vs configurable | Rubric items it decides |
|---|---|---|---|---|
| Idea capture | `capture.py:capture_ideas` | none (mechanical split) | — | nothing decides quality here |
| Discovery (4 lanes) | `trends.py` — `MOMENTS_PROMPT`, `EVERGREEN_PROMPT`, `TRENDING_PROMPT`, `RAGEBAIT_PROMPT` | hardcoded in file | hardcoded | #8 (topic sourcing), #5 (ragebait lane), partially #9 (explicitly *can't* see sounds — `LIMITATION`, `trends.py:27-30`) |
| Live meta | `trends.py:scan_meta` → `meta_notes` | `META_PROMPT` hardcoded | 7-day staleness constant | #11 (external evidence in) |
| Fan-out | `pipeline.generate_takes` + `TAKES_CONTRACT` | hardcoded; playbook file appended | playbook/bible are editable files | #1 (hook per take), #4/#5 (share_identity, precedent) |
| Concept dev | `pipeline.develop_concept` + `BRIEF_CONTRACT` | hardcoded | — | human steering |
| Full write | `pipeline.run_pipeline_for_idea`; system = `content_engine_prompt.md` + `brand_bible.md` + `viral_playbook.md` + `JSON_CONTRACT` (`pipeline.py:223-247`) | files + hardcoded contract | prompt files editable; contract hardcoded | #1,#2,#3,#4,#5,#7,#10 |
| QA gate | model self-scores in-call; `models.Post.qa_failures` enforces thresholds (`models.py:86-93`) | thresholds hardcoded | hardcoded | supposedly #1–#5 — **see the neutering finding** |
| Human review | `/review`, `/api/ideas/{id}/edit`, `approve` (`webapp.py:428-434, 539-612`) | — | — | human override |
| Image gen | `images.render_slide` → `compose_design_prompt`; per-slide prompt = refs clause + slide `image_prompt` + house style + `brand.style_prompt` + design system + sequence block + text rules | mix: engine-written `image_prompt` + several hardcoded blocks | `brand.toml` + settings profile configurable; text rules and dots hardcoded | #1 (slide-1 visual), #6 |
| Scroll test | `scrolltest.py:SYSTEM_PROMPT`, judged by `judge_model` on rendered slide 1 | hardcoded | model configurable | #1 — the only *independent* check in the pipeline |
| Learning | `learning.py:performance_notes` injected into builds/takes/scroll-test | generated from logged metrics | thresholds hardcoded (`MIN_POSTS_FOR_NOTES=3`) | #11 |
| Export | `publisher.MetricoolCSVPublisher`, `/api/ideas/{id}/manual` | — | `config/metricool_columns.toml` | #10 delivery, #9 (absent) |

### The exact prompts (the load-bearing quotes)

**The write contract** — `pipeline.py:JSON_CONTRACT` (appended to every build):
- Hook: `"hook": "string — the STRONGEST of the three hook_options"` with 3 options returned (line 48-51).
- Per slide: `role` ("hook / recognition / escalation / payoff / cta"), `swipe_trigger` ("the OPEN LOOP this slide leaves dangling… Every non-final slide MUST hand off to the next one") (lines 57-58).
- Image prompt: *"a COMPLETE graphic-design brief for this slide as a piece of scroll-stopping social media art … Think **agency-level concept art** for TikTok."* (line 59).
- Comment: *"one comment prompt that is EFFORTLESS and SELF-DEFINING to answer … Never 'what do you think?'"* (line 67).
- QA: *"Apply the QA thresholds and REVISE internally before returning — **only emit a post you would pass**."* (lines 97-99).

**The engine instructions** — `content_engine_prompt.md`:
- Stage 0 psychology (lines 28-37): 200ms thumb decision, curiosity wage, share-as-identity, high-arousal only, named-unnamed, specifics, effortless comments, peak-end. Excellent.
- Stage 3 hooks (lines 59-64): max ~10 words, banned vague hooks list. Excellent.
- Stage 6 (lines 100-103): *"Slide 1 is the strongest visual and establishes the world"* — but framed entirely as a **designed sequence** ("same palette, same type treatment, same recurring motif… one designed sequence, not five separate posts").

**The image layer** — `images.py`:
- `_sequence_block` (lines 336-341): *"Include a small, consistent progress indicator in the same corner on every slide — a discreet row of N dots… it is the only extra graphic element allowed."* Hardcoded, unconditional, on slide 1 too.
- `_DESIGN_TEXT_RULES` / `_EMBEDDED_TEXT_RULES` (lines 182-226): hardcoded 12% margins, headline-upper-third, exact-copy rules.
- `render_mode_for_idea` (lines 553-561): **always returns `"ai_design"`** — the stored per-post `render_mode` the web API still accepts and validates (`webapp.py:766-769`) is silently ignored. Documented in a docstring the UI never shows.
- `profile.profile_style_block` (`profile.py:154-172`): *"LOCKED BRAND LOOK — … must be IDENTICAL on every slide of every post … Do NOT reinvent it or drift."* Injected into **every** slide prompt once the settings profile has any visual field.
- `brand.toml:40` fallback consistency clause hardcodes *"Part of a 5-image set"* regardless of actual slide count (used when a post has no design_system).

### Hardcoded instructions silently overriding creative direction (the hunt you asked for)

1. **The QA gate is prompt-neutered.** `pipeline.py:97-99` tells the model to only emit posts it would pass. Since the same model grades its own homework in the same call, `Post.qa_failures()` is nearly always empty → the retry (`run_pipeline_for_idea`, one re-request) almost never fires, and the review flag almost never trips. The gate exists in code (`models.py:124-149`) but the prompt instructs the model to make it unreachable.
2. **"Agency-level concept art"** (`pipeline.py:59`) silently sets the aesthetic of every image brief the engine writes — before `brand.toml`, before the house style, before anything the editor chooses.
3. **`render_mode_for_idea` hardcoded to `ai_design`** (`images.py:553-561`) — the UI's `render_mode` choice is dead.
4. **Mandatory progress dots on every frame** (`images.py:336-341`) — unconditional carousel-ad furniture, including on the slide-1 thumbnail.
5. **LOCKED BRAND LOOK "identical on every post"** (`profile.py:154-172`) — whatever the editor types into settings becomes a poster template stamped on every frame of every post. Combined with (2), this is the machine that makes output look like a branded ad series.
6. **`quality_first = "medium"`, `variants_first = 1`** (`brand.toml:19-22`) — the highest-leverage image in the pipeline gets one mid-quality roll. (ROADMAP.md line 18 claims "slide 1 `high`" — the config disagrees.)
7. **`LENGTH_PREFS["quick"] = "roughly 1–2 slides"`** (`pipeline.py:252-258`) and `MIN_SLIDES = 1` (`models.py:80`) — the system happily ships 1–2 slide "carousels", which can't earn carousel completion mechanics at all.

---

## Phase 2 — Diagnosis against the 11-item rubric

### 1. Slide 1 as thumbnail + scroll-stopper — **Severity: CRITICAL**
**Copy side: strong.** Stage 3 (`content_engine_prompt.md:59-64`) bans vague hooks, demands a plantable question, caps length; the contract returns 3 hook options (`pipeline.py:49-51`); `route.psych.hook_question` forces the curiosity gap to be written down (`pipeline.py:75`).
**Visual side: this is where the cap lives.** Slide 1 is generated once, at medium quality (`brand.toml:19-22`), from a brief whose north star is "agency-level concept art" (`pipeline.py:59`), wearing the locked brand look and a progress-dot badge. It is not logo-first — there is no wordmark in `ai_design` mode (the brand furniture in `compose_slide` only runs in the dead overlay path) — but it *is* **designed-artefact-first rather than stop-first**: a stranger sees a polished AI brand graphic, which on 2026 TikTok is scroll fuel. The one honest check (`scrolltest.py` — genuinely well-built: adversarial, defaults to scroll, calibrated on the account's own history) is opt-in via a button (`webapp.py:654-666`), never auto-runs, and its verdict is written only into the transient job row — never onto the idea, never into learning, never into a regeneration.
**Gap:** the highest-leverage 200ms of the product has one dice-roll, an ad-flavoured brief, and no enforced adversarial check.

### 2. Completion rate / slide count & payload — **Severity: HIGH**
**Handled:** per-slide `role` + `swipe_trigger` with an anti-reorder test (`content_engine_prompt.md:83`), variable density via `body` reader slides (line 91), Architect stage owns count (lines 66-72), engine range 1–10 (`models.py:80-81`).
**Gap:** (a) the 1–2 slide allowance ("quick" pref, `pipeline.py:252-258`) is anti-completion — a 2-slide photo post can't build the swipe-through signal the algorithm measures; the 2026 sweet spot of 5–10 appears nowhere as guidance. (b) Nothing verifies per-slide payload *after* rendering — QA scores the copy plan, and (see #1) QA is neutered anyway. Slide-level "is this slide skippable" has no independent judge.

### 3. Forced swipes / momentum — **Severity: LOW**
**Handled well.** `swipe_trigger` per slide (`pipeline.py:58`), the through-line spine (`content_engine_prompt.md:83`), `design_system.evolution` for visible motion (`pipeline.py:85`), sequence block "leave clear visual momentum pulling the viewer to swipe" (`images.py:331-335`), reference-continuity rendering with slide 1 as pixel anchor and pan mode (`images.py:1113-1153`). Numbered-list/"the last one…" shapes live in `viral_playbook.md` (#5 tier list, #4 flags with "deliberately arguable" last flag).
**Gap:** minor — momentum is engineered in words and design but never tested (no check that slide N actually opens a loop).

### 4. Saves & shares — **Severity: HIGH**
**Handled in generation:** `route.psych.share_identity` forces "sending this says ___ about me" (`pipeline.py:76`); `saveability` is a QA dimension; reader `body` slides exist for save-worthy density; two mechanics skeletons end in "save-this prompt" (`config/mechanics.toml:27,61`); the playbook maps shapes to saves (#9/#10/#11).
**Gaps:** (a) The engagement gate requires only **one of four** engagement scores ≥8 (`models.py:87-93`) — a post can ship with zero save *and* zero share intent as long as comment_fight scores. (b) There is **no explicit save/share CTA requirement** — "save this / screenshot this" and "send this to a gym mate who…" are options the model *may* choose as the last slide's social action (`pipeline.py:58`), never a per-post decision that's made and checked. (c) Nothing measures which CTA type was used, so learning can't correlate it (metrics fields exist for saves/shares, `learning.py:25`, but the trait system only tracks category/mechanic/style, `learning.py:70-78`).

### 5. Comment bait — **Severity: CRITICAL (delivery), LOW (generation)**
**Generation is excellent:** dedicated `comment_trigger` with the right rules — self-categorisation, slightly-wrong ranking, confession, "answerable in under 5 words", "'what do you think?' is a dead prompt" (`pipeline.py:67`, `content_engine_prompt.md:36`). The ragebait lane (`trends.py:RAGEBAIT_PROMPT`) engineers the fight deliberately with a split test and rules of the fight.
**Delivery is broken:** `build_caption` (`publisher.py:93-105`) assembles caption + hashtags only. `api_manual_post` (`webapp.py:1372-1403`) does the same. **The comment_trigger never leaves the app through either posting path.** Unless the editor happens to copy it by hand from the review screen, the pipeline's entire comment-bait machinery produces a string that dies in SQLite.

### 6. Native > polished — **Severity: CRITICAL**
This is the aesthetic the whole image layer pushes *against*:
- "agency-level concept art", "one designed template", "designed, scroll-stopping experience, not five separate posters" (`pipeline.py:59`, `content_engine_prompt.md:85,101`, `images.py:451-454`).
- LOCKED BRAND LOOK identical every post (`profile.py:154-172`).
- Progress dots on every frame (`images.py:336-341`).
- Embedded typography rendered as brand-world objects — "printed on a shaker or supplement label… gym neon" (`brand.toml:73`).
The counter-evidence exists in the codebase but is sidelined: `brand.toml` has `gritty` and `meme` presets ("shot on a phone with harsh direct flash… authentic not staged"; "deliberately mundane amateur photo look") — but the style picker was **removed from the create journey** (`webapp.py:320-323`: "No style preset picker: the engine designs a bespoke design_system per post"), so `route.style` is almost always empty and those prompts never run. The trending lane correctly hunts screenshot/notes-app/photo-dump formats (`trends.py:407-417`) — and then the render layer converts them into designed AI posters. The engine prompt's own auto-fail list bans "generic visual (person lifting / supplement tub / neon…)" (`content_engine_prompt.md:98`) but never bans *looking like an ad or AI art* — the actual 2026 death sentence.

### 7. Value-first, product-second — **Severity: LOW**
**Handled well, consistently.** "Supplement education → never a lecture" (`content_engine_prompt.md:44`); moment rules make the moment the star with an auto-reject for "80% supplement talk wearing a thin topical hat" (lines 45, 98); `brand_bible.md` defines the narrator as comedy-first, "not a supplement advert"; the evergreen lane requires a believable bridge, never a hard sell (`trends.py:341-369`). The gold-standard examples are 100% viewer-world. No meaningful gap in the prompts; the risk sits in #6 (the *visuals* selling harder than the words).

### 8. Topic/niche consistency (content pillars) — **Severity: MEDIUM**
**Not enforced anywhere.** `content_category` is free text stamped by whichever door created the seed ("trend", "moment", "fact", "ragebait", or blank — `webapp.py:1276-1301`, `capture.py`). There is no defined pillar list, no target mix, no check that a week's output clusters into something the algorithm can classify. The brand bible's "running themes it owns" (`brand_bible.md:30-33`) is the germ of a pillar system, but nothing reads it as one. The calendar shows dates, not pillar balance.

### 9. Trending sound — **Severity: MEDIUM. Not handled anywhere.**
The system is honest that it can't see TikTok audio (`trends.py:27-30` `LIMITATION`, repeated in every scout prompt, and `content_engine_prompt.md:129`). But it also does nothing with the fact: no "add a trending sound before posting" step in the manual-post payload (`webapp.py:1372-1403`), no sound-vibe suggestion field, no checklist. Photo Mode posts with a trending sound measurably outperform silent ones; right now the operator has to remember unprompted. (`audio_mode` in `config.py:77` is video-only, M6, off — correctly out of scope.)

### 10. Caption + hashtags — **Severity: LOW-MEDIUM**
**Handled:** caption contract is stranger-aware (title line + 1-2 sentences driving a comment/save, `pipeline.py:66`); hashtags "6-10… broad UK gym/fitness reach tags and 2-3 niche/topical" (`pipeline.py:68`); profile `default_hashtags` always-include (`profile.py:115-119`); editor-editable in review.
**Gaps:** (a) the comment/save driver in the caption is undermined by #5's lost comment_trigger; (b) `build_caption` collapses all newlines for the CSV path (`publisher.py:96-98` — a real Metricool constraint, but it means the exported caption loses the title-line structure the contract worked for; the manual path keeps it, `webapp.py:1377-1379`); (c) no hashtag learning — winners' tags are never correlated (only category/mechanic/style traits, `learning.py:70-78`).

### 11. Volume + iteration + feedback loop — **Severity: HIGH (present but under-plumbed)**
The brief guessed this was absent. It isn't — and that matters for the fix:
- **Exists:** one-tap 🔥/😐/💀 rating + full metric logging (`webapp.py:1407-1450`), honest small-n trait analysis (`learning.py` — rating-first, topical-win caveat, `MIN_TRAIT_POSTS` guard), `performance_notes` injected into every build (`pipeline.py:614-621`), every fan-out (`worker.py:214-226`), and the scroll test's calibration (`scrolltest.py:229-234`). Weekly auto-refreshed meta research feeds concept work (`trends.py:scan_meta`, `worker.py:211-213`). A weekly systemd timer supports cadence (`deploy/chrgd-run.timer`).
- **The gaps:** (a) logging is entirely manual with **no nudge** — nothing flags an exported post that's never been rated, so at 70 followers the loop will starve silently; (b) the trait vocabulary is category/mechanic/**style** — and style is now nearly always empty (picker removed), so a third of the learning dimensions is dead; (c) scroll-test verdicts and QA scores are never joined with real results, so the system can't learn whether its own judges predict anything; (d) hook type, CTA type, slide count and visual format — the traits the rubric says drive reach — are not tracked at all; (e) `POST_ANALYSIS_PROMPT.md` (the deep analysis) lives outside the app as a copy-paste ritual.

---

## Scorecard

| # | Rubric item | Where it lives | Grade | Severity of gap |
|---|---|---|---|---|
| 1 | Slide 1 scroll-stopper | prompt: strong · image: `brand.toml:19-22`, `pipeline.py:59` | copy A / visual D | **Critical** |
| 2 | Completion / count & payload | `content_engine_prompt.md:66-91`, `pipeline.py:252-267` | B- | High |
| 3 | Forced swipes / momentum | `pipeline.py:58`, `images.py:297-342` | A- | Low |
| 4 | Saves & shares | `pipeline.py:76`, `models.py:87-93` | C+ | High |
| 5 | Comment bait | gen: `pipeline.py:67` · delivery: `publisher.py:93-105` | gen A / delivery F | **Critical** |
| 6 | Native > polished | `pipeline.py:59`, `profile.py:154-172`, `images.py:336-341` | D | **Critical** |
| 7 | Value-first | `content_engine_prompt.md:44-45`, `brand_bible.md` | A | Low |
| 8 | Content pillars | not enforced anywhere | D | Medium |
| 9 | Trending sound | not handled anywhere | F (honest F) | Medium |
| 10 | Caption + hashtags | `pipeline.py:66-68`, `publisher.py:93-105` | B | Low-Med |
| 11 | Feedback loop | `learning.py`, `scrolltest.py` — present, starved | C+ | High |

The prescriptions, with rewritten prompts and diffs, are in `CHANGE_PLAN.md`.
