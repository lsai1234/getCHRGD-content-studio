# CHANGE_PLAN — prioritised, phased, additive

Companion to `REVIEW.md`. Ordered by leverage: Phase 1 attacks the 300-view cap directly (slide-1 stop power + native aesthetic + un-neutered QA), Phase 2 delivers the engagement signals already generated, Phase 3 closes the structural gaps (pillars, learning), Phase 4 is polish. Everything is additive or a config/prompt change — no working flow breaks. Carousels only throughout; where architecture should leave room for video it's noted and nothing more.

---

## Phase 1 — Break the 300-view cap (slide 1 + native aesthetic + honest QA)

### 1.1 Slide 1: three high-quality rolls, not one medium one
**Lever:** #1 scroll-stop → completion. Slide 1 is ~all of the first-pool verdict; one medium-quality dice roll is malpractice.
**Where:** `brand.toml` `[generation]` (read by `Generation.quality_for` / `variants_for`, `chrgd/brand.py:62-66`). The variant-picking UI already exists (`images.render_slide` variants, `pick_variant`, create-journey picker) — this is pure config.

```diff
 [generation]
 size = "1024x1536"
-quality_first = "medium"
+quality_first = "high"
 quality_rest = "low"
-variants_first = 1
+variants_first = 3
```

Cost: 3 × high ≈ $0.50/post for slide 1 (vs $0.04 today, `_IMAGE_COST`, `images.py:33`). That is the correct place to spend.

> **Revision (shipped):** the editor chose to keep **one** high-quality slide-1 image (`variants_first = 1`, `quality_first = "high"`) rather than three, and traded the extra rolls for an upstream quality gate. A new **slide-1 concept gate** (`chrgd/conceptgate.py`) now runs inside `services.render_idea` **before** the image spend: an independent judge (the cheap `judge_model`) scores the slide-1 concept — its on-image headline + the visual brief — out of 10; below `CHRGD_CONCEPT_GATE_MIN` (default 9) a creative pass sharpens slide 1 against the judge's named weakness and it's re-judged, up to `CHRGD_CONCEPT_GATE_ROUNDS` (default 3). Cheap text calls guard the one expensive image. The verdict is persisted to `route_json.concept_gate`, surfaced in `api_idea_detail`, streamed as live render notes, and shown as a badge in the create journey. It's config-gated (`CHRGD_CONCEPT_GATE`) and never blocks a render on an LLM hiccup. This is complementary to the post-render scroll test (1.2): concept judged before spend, pixels judged after.
>
> **Revision 2 (shipped):** scoring-and-sharpening one opener turned out to be the wrong shape on its own — a refine loop only ever improves the concept it was handed, so it makes the same idea *louder*, never *cleverer*, because it never leaves the neighbourhood of whatever the build happened to write first. The gate now runs four stages. **A. Rivals** — a creative call invents `CHRGD_CONCEPT_CANDIDATES` (default 4) genuinely different openers for the same post, each forced onto a different curiosity mechanic from a fixed menu, each having to name the exact question it plants and what stops it being the obvious opener; it is shown slide 2 so the opener still has to set up the post. **B. Tournament** — the judge scores the whole field (rivals + the built opener as incumbent) on intrigue / originality / instant legibility and picks what to spend on; this is where "a hundred other gym accounts could open with this" actually gets caught, because in isolation a derivative opener scores fine and next to three sharper rivals it loses. A winner index the judge invents out of range falls back to the highest-ranked candidate, never silently to candidate 0. **C. Score** — the existing brutal solo judge and bounded sharpen loop, now returning `intrigue`/`originality`/`instant` sub-scores so a refine has a specific lever, with a hard rule that a competent-but-familiar opener caps at 6. **D. Glance** — the "prove it" step: the judge is shown *only* what a stranger perceives in the half-second before the thumb moves (the on-image words and the shape of the picture — no topic, no intent, no reasoning) and has to say what it actually took away; a fail buys exactly one targeted re-cut and an honest second verdict. Stages A/B and D are separately config-gated (`CHRGD_CONCEPT_TOURNAMENT`, `CHRGD_CONCEPT_GLANCE`) and individually contained — a failed tournament still leaves the solo score to run, and nothing here can block a render. The field, the losers' epitaphs and the glance verdict are persisted to `route_json.concept_gate` and shown in the create journey.

### 1.2 Auto-run the cold scroll test after every render, persist the verdict, act on it
**Lever:** #1. The only adversarial judge in the pipeline must be mandatory, not a button.
**Where:** `worker.py:_handle_render` (and `_handle_render_slide` when `slide == 0`), `worker.py:_handle_scroll_test`.

```diff
 def _handle_render(store: Store, settings: Settings, job: dict) -> dict:
     ...
     result = render_idea(
         store, settings, idea, dry_run=dry_run, on_slide=on_slide, notify=notify
     )
+    # Every finished render gets the adversarial slide-1 verdict, automatically.
+    if not dry_run and result.paths:
+        enqueue_scroll_test(store, idea.idea_id)
     return {...}
```

```diff
 def _handle_scroll_test(store: Store, settings: Settings, job: dict) -> dict:
     ...
     verdict = run_scroll_test(idea, settings, store)
+    # Persist onto the idea so the UI, the calendar and the learning loop can
+    # see it — a verdict that lives only in a job row teaches nothing.
+    route = json.loads(idea.route_json) if idea.route_json else {}
+    route["scroll_verdict"] = verdict.model_dump(mode="json")
+    store.conn.execute(
+        "UPDATE ideas SET route_json = ? WHERE idea_id = ?",
+        (json.dumps(route), idea.idea_id),
+    )
+    store.conn.commit()
     return {"verdict": verdict.model_dump(mode="json")}
```

Then surface it: `api_idea_detail` returns `route.scroll_verdict`; the create journey shows STOP/SCROLL + the fix on the finished screen; a "scroll" verdict renders the one-click action the verdict already names (`fix_kind` = `regenerate_image` → slide-1 regen with the fix appended to the prompt; `sharpen_hook` → the suggested hook pre-filled in the hook picker). Keep it one automatic loop maximum — verdict → one regen — human decides after that, so spend can't run away.

### 1.3 Kill "agency-level concept art": rewrite the image-brief instruction to native-first
**Lever:** #6 native > polished, #1. This one sentence sets the aesthetic of every image brief the engine writes.
**Where:** `pipeline.py:59`, the `image_prompt` field description in `JSON_CONTRACT`. Replace the current text with:

> `"image_prompt": "string — a COMPLETE visual brief for this slide that a stranger's feed would read as NATIVE TikTok content, not an advert. Default to the platform's native visual formats: a candid phone photo (harsh flash, awkward framing, real UK gym), a screenshot-style artefact (notes app, group chat, poster on a gym wall), a meme-shaped image, or a photo-dump frame. Reserve designed/illustrated treatments for posts whose CONCEPT is the design (a tier chart, a fake document). Write it as ONE FRAME of the shared design_system below — same palette, recurring motif and type treatment as its siblings — but the continuity should feel like the same person's camera roll, not a branded template. Say what has CHANGED from the previous frame so the swipe shows motion. HARD RULE: if the finished image could pass for a supplement brand's paid ad or generic AI art, the brief has failed — rewrite it. Do NOT invent text beyond the approved slide copy."`

And in `content_engine_prompt.md` stage 6 (line 101), append one sentence:

> **The native test (hard):** before finalising any image prompt, ask — would this frame look at home between two normal people's posts on the FYP, or does it look like a brand made it? Polished, symmetrical, "designed" output reads as an ad and dies in the first test pool. Raw, candid, screenshot-native, slightly imperfect wins.

And add to the stage 5 auto-fail list (`content_engine_prompt.md:98`): **"a visual set that reads as a branded ad template or obvious AI art rather than native content"**.

### 1.4 Make the progress dots opt-in (default off)
**Lever:** #6. Carousel-ad furniture on every frame, including the thumbnail.
**Where:** `images.py:_sequence_block` (lines 336-341) + `brand.py:Generation`.

```diff
 class Generation(BaseModel):
     ...
     reference_continuity: bool = True
+    # A small progress-dot row on each slide. Off by default: it reads as
+    # carousel-ad furniture to a cold viewer; TikTok's own UI already shows
+    # position.
+    progress_dots: bool = False
```

```diff
-    out.append(
-        f"Include a small, consistent progress indicator in the same corner on "
-        f"every slide — a discreet row of {total} dots with dot {index + 1} "
-        "highlighted — so the viewer feels their place in the journey. Keep it "
-        "tiny and tasteful; it is the only extra graphic element allowed."
-    )
+    if show_dots:  # brand.generation.progress_dots, passed in by the caller
+        out.append(
+            f"Include a small, consistent progress indicator in the same corner on "
+            f"every slide — a discreet row of {total} dots with dot {index + 1} "
+            "highlighted. Keep it tiny; it is the only extra graphic element allowed."
+        )
+    else:
+        out.append(
+            "Add NO page indicators, dots, numbers or frame counters — the "
+            "platform UI shows position."
+        )
```

(`_sequence_block` gains a `show_dots: bool` parameter; `compose_design_prompt` passes `brand.generation.progress_dots`. The `_DESIGN_TEXT_RULES` sentence "The ONLY non-copy graphic allowed is the small progress-dot indicator described above" becomes conditional the same way.)

### 1.5 Soften the brand lock from "poster template" to "recognition accents"
**Lever:** #6, without losing brand recognisability.
**Where:** `profile.py:profile_style_block` (lines 154-172). Rewrite the framing (the field plumbing is untouched):

```diff
-    return (
-        "LOCKED BRAND LOOK — this is the account's fixed visual identity and "
-        "must be IDENTICAL on every slide of every post (same aesthetic, same "
-        "colours, same typography). Do NOT reinvent it or drift — only the "
-        "specific scene changes post to post. ..."
+    return (
+        "BRAND RECOGNITION ACCENTS — carry these so the account is recognisable, "
+        "but as accents inside NATIVE-looking content, never as a poster "
+        "template: the palette appears as an accent (a prop, a light, one "
+        "graphic element), not a wall-to-wall colour scheme; the typography "
+        "treatment applies where text is designed, not forced onto candid "
+        "frames; the recurring character/motif appears where the scene calls "
+        "for them. The frame must still pass as native TikTok content first, "
+        "brand asset second. ..."
     )
```

Apply the same shift to `profile_design_lock` (`profile.py:175-188`): palette/character stay fixed values, but the instruction says "woven into native frames", not "identical on every slide".

### 1.6 Un-neuter the QA gate
**Lever:** #1/#2/#4/#5 — every threshold in `models.py` is currently decorative.
**Where:** `pipeline.py:97-99` (end of `JSON_CONTRACT`). Replace:

> `All QA scores are integers 0–10 and must reflect the honest, brutal QA stage. Apply the QA thresholds and REVISE internally before returning — only emit a post you would pass. Use only the approved slide text inside image prompts.`

with:

> `All QA scores are integers 0–10 and must reflect the honest, brutal QA stage. Revise internally first — but then SCORE THE POST YOU ACTUALLY WROTE, honestly. Do NOT inflate scores to clear the thresholds: a true 6 reported as 6 gets this post a targeted rewrite; a 6 reported as 9 ships a weak post to a real audience and poisons the account's data. Honest failure is cheap, dishonest passing is expensive. Use only the approved slide text inside image prompts.`

The existing retry loop (`run_pipeline_for_idea`, one re-request with the failure reasons) and the review flag then start doing their jobs. Expect a modest increase in second attempts (~2× token cost on the minority of builds that honestly fail) — that's the gate working.

### 1.7 Slide-count guidance: aim at the completion sweet spot
**Lever:** #2. Keep the Architect's freedom; move the centre of gravity.
**Where:** `content_engine_prompt.md:66-72` — change the length guidance bullets to:

> - **The completion sweet spot is 5–8 slides.** Enough swipes to register a strong completion signal, short enough to finish. Default here.
> - A single killer visual gag might justify **2–3** — but understand a 1–2 slide post barely registers as a carousel to the algorithm; use only when the punch genuinely dies if stretched.
> - A listicle, ranking or meaty deep-dive can earn **9–10** — only if every extra slide adds a fresh hit.

And `pipeline.py:252-258`, `LENGTH_PREFS["quick"]`: change "roughly 1–2 slides" to "roughly 3–4 slides — a fast punch that still gives the algorithm swipes to count. Go below 3 only if the idea genuinely dies stretched."

---

## Phase 2 — Deliver the engagement signals you already generate

> **Status: shipped.** 2.1 delivers the `comment_trigger` as a pinnable first comment in the manual-post flow (both the create journey and the calendar modals) and, config-gated, as a CSV column (`[header] first_comment` in `config/metricool_columns.toml`, off by default). 2.2 adds a declared `engagement_play` (save/share/comment) to the write contract + an auto-fail when the copy doesn't execute it, surfaced in `api_idea_detail`. 2.3 adds the pre-post checklist (trending sound, pin the first comment, early replies) to the manual-post payload and both modals.

### 2.1 Comment trigger → pinned first comment, both posting paths
**Lever:** #5. Generation is excellent; delivery is zero.
**Where:** `webapp.py:api_manual_post` (lines 1372-1403) and `publisher.py`.

Manual path (the one actually used at 70 followers):

```diff
     caption_text = caption + (f"\n\n{tag_str}" if tag_str else "")
     return {
         "idea_id": idea_id,
         "caption_text": caption_text,
+        # Pin this as the post's first comment the moment it's live — the
+        # comment section's opening move is part of the post.
+        "first_comment": (idea.comment_trigger or "").strip(),
         "images": images,
         "count": len(images),
     }
```

…and the manual-post modal gets a second copy-button: "First comment (pin it)".

CSV path: add an optional first-comment column to `config/metricool_columns.toml` (`[header] first_comment = ""` — empty means the plan doesn't support it and the column is skipped), and in `publisher._row_for`: `if c.header.first_comment: row[c.header.first_comment] = idea.comment_trigger or ""`. Config-gated, so nothing changes until the column name is set to match the Metricool template.

### 2.2 Require an explicit save/share move per post
**Lever:** #4. Today "save this"/"send this to…" is one option the model may pick; make it a decision that's always made.
**Where:** `pipeline.py` `JSON_CONTRACT` — add one field to the top-level object (additive; `Post.route` is an open dict so old rows are unaffected — add it inside `route`):

> `"engagement_play": "string — the ONE deliberate off-screen action this post is built to earn, chosen and named: 'save' (reference/checklist value — the final slide or caption must literally say save/screenshot this), 'share' (the caption or final slide names WHO to send it to: 'send this to the mate who…'), or 'comment' (the comment_trigger is the play). Every post declares exactly one primary play and executes it explicitly in the copy."`

And in stage 5 auto-fails (`content_engine_prompt.md:98`): **"a post whose declared engagement_play never appears in the actual copy"**. Store it in `route_json` (free — `build_fields_from_post` already serialises `route`), so learning can correlate play-type with results later (Phase 3).

### 2.3 Posting checklist, including the trending-sound step
**Lever:** #9 (and #5's pin step). The system rightly refuses to guess sounds; it should still make the operator do the 20-second in-app step.
**Where:** `webapp.py:api_manual_post` + the manual modal. Add to the payload:

```python
"checklist": [
    "Add a trending sound in the TikTok app before posting — Photo Mode posts with trending audio outperform silent ones (browse Trending in the sound picker; pick something low-vocal).",
    "Pin the first comment (copied above) right after posting.",
    "Reply to the first few comments within the hour — early replies feed the same signals.",
],
```

Pure UI/JSON — no model calls, no schema change. (Architecture note per the constraints: if video lands later, the same checklist mechanism carries video-specific steps; nothing to build now.)

---

## Phase 3 — Structural: pillars + a feedback loop that actually learns

> **Status: shipped.** 3.1 adds `BrandProfile.pillars` (settings textarea), rendered into `profile_engine_block` (so it reaches every concept + build), and a `pillar` field + rule in the takes fan-out that rides `route_json` with the chosen take. 3.2 expands the learning trait vocabulary from category/mechanic/style to **pillar, engagement_play and slide-count bucket** (`learning.SKEW_TRAITS`), adds a `needs_rating` nudge on calendar cards to stop the loop starving, and adds `scroll_calibration` (how often the pre-render scroll test agreed with real hit/flop ratings), surfaced in the insights panel. 3.3 adds `chrgd/analytics.py` + `POST /api/metrics/import` + a calendar upload control: a tolerant TikTok-analytics-CSV parser that matches rows to posts by caption and bulk-fills `metrics_json` (merging, so ratings survive), no LLM.

### 3.1 Content pillars, enforced at concept time
**Lever:** #8. The audience-classification problem: scattered topics never build an audience profile.
**Where:** `profile.py` + `pipeline.py` + `worker.py`.

1. `BrandProfile` gains `pillars: str = ""` (newline list, e.g. `gym culture comedy / archetypes`, `energy & caffeine truths`, `training myths`, `UK moments`). Settings page textarea. Ignore-unknown-keys loading (`profile.py:75-82`) makes this migration-free.
2. `profile_engine_block` renders it: `"Content pillars (EVERY post belongs to exactly one — name it): …"`.
3. `TAKES_CONTRACT` (`pipeline.py:921-968`) gains a `"pillar"` field per take + rule: *"Every take names its pillar from the brand profile. A take that fits no pillar is off-strategy — replace it. Across the fan-out, cover at least two different pillars."*
4. The chosen take's pillar rides `route_json` (already happens — the whole take is stored, `webapp.py:945-982`), so it lands in the learning loop for free.

### 3.2 Feed the learning loop the traits that matter, and stop it starving
**Lever:** #11.
**Where:** `learning.py`, `webapp.py`.

1. **Trait vocabulary:** `_traits` (`learning.py:70-78`) currently reads `category / mechanic / style` — style is dead (picker removed). Add: `pillar` (from `route.take.pillar` / route), `engagement_play` (from 2.2), `slide_count` (bucketed: `1-3` / `4-6` / `7-10`, from `slides_json`), and `scroll_verdict` (from 1.2 — now the system learns whether its own judge predicts reality).
2. **Stop the starvation:** the calendar card builder (`webapp.py:_card`) already reads metrics; add `needs_rating: exported and no rating and >3 days old`, render a one-tap 🔥/😐/💀 row directly on the card. The rating endpoint exists (`webapp.py:1435-1450`); this is purely making the loop unavoidable.
3. **Close the judge loop:** when a rated post's `scroll_verdict` said "stop" but it flopped (or vice versa), `insights()` reports it ("the scroll judge is running X/Y against your real results") — an honest calibration line, no new ML.

### 3.3 In-app analytics import (replaces the copy-paste ritual)
**Lever:** #11. `POST_ANALYSIS_PROMPT.md` proves the analysis works; it just lives outside the app.
**Where:** new small endpoint + parser: upload the TikTok Analytics CSV (per-post views/likes/shares, ~60 days), match rows to exported ideas by date/caption prefix, bulk-fill `metrics_json` via existing `store.set_metrics`. One file, ~100 lines, no LLM. (The deep LLM analysis stays in the chat workflow for now — fine.)

---

## Phase 4 — Polish / consistency

> **Status: shipped.** (1) The fallback consistency clause is now count-agnostic ("Part of one carousel set…") in `brand.toml` + `brand.py`. (2) The dead `render_mode` input plumbing is removed — the form fields, `_new_seed` validation/storage, the `creation_prefs` key and the create.html payload field all gone; `render_mode_for_idea` (always `ai_design`) and the derived detail field stay. (3) ROADMAP's slide-quality line now matches config (slide 1 `high`, rest `low`). (4) The export screen warns that bulk-imported captions lose line breaks and points to manual posting. (5) Was already delivered in §1.3 — the native `gritty`/`meme` treatments are quoted in the stage-6 native guidance.

1. **Fix the 5-image hardcode:** `brand.toml:40` consistency clause says "Part of a 5-image set" regardless of count. Make `images.py` format it with `len(slides)` or drop the number ("Part of one image set…").
2. **Resolve the render_mode dead switch:** either delete the `render_mode` form field + validation (`webapp.py:766-769`) or make `render_mode_for_idea` honour it. Recommend deleting the field — one mode, one truth (`images.py:553-561` docstring already says so).
3. **Config/docs mismatch:** ROADMAP.md line 18 says slide 1 renders `high`; `brand.toml` said `medium` (fixed in 1.1 — update the doc).
4. **Caption newline note:** the CSV path collapses the caption's title-line structure (`publisher.py:96-98`, real Metricool constraint). Add a line to the export screen: "bulk-imported captions lose line breaks — posts that lean on caption formatting are better posted manually."
5. **Restore native style presets as engine vocabulary:** `gritty`/`meme` prompts in `brand.toml` are good and currently unreachable. Rather than resurrecting the picker, quote them inside the rewritten stage-6 native guidance (1.3) as named treatments the engine may invoke per slide.

---

## Sequencing & safety

| Order | Change | Type | Risk |
|---|---|---|---|
| 1 | 1.1 quality/variants | config | none |
| 2 | 1.3 + 1.4 + 1.5 native shift | prompt/config | aesthetic shift is the point; variants (1.1) give the human 3 options to judge it on |
| 3 | 1.6 honest QA | prompt | more retries on weak posts (intended); spend cap already bounds it |
| 4 | 1.2 auto scroll test | code (additive) | one extra cheap judge call per render |
| 5 | 1.7, 2.1–2.3 | prompt + small code | additive; CSV column config-gated |
| 6 | Phase 3 | code | additive columns/JSON only, no migration |
| 7 | Phase 4 | cleanup | trivial |

No large refactor anywhere — the biggest single diff is ~40 lines (1.2). Every change is per-file revertible, and the prompt changes can be A/B'd the honest way: ship 3–5 posts/week, rate them, and let `learning.py` — now fed real traits — tell you which shapes escaped the pool.
