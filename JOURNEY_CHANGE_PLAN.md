# JOURNEY_CHANGE_PLAN — prioritised, phased, additive

Companion to `JOURNEY_REVIEW.md`. Phase 1 is the virality engine (prompt rewrites — highest leverage, zero schema breaks). Phase 2 is performance (the worker + fetch architecture). Phase 3 is UX. Nothing here breaks a working flow; the one structural change (worker split) is flagged with its tradeoffs before any code.

---

## Phase 1 — The virality engine (prompts + model routing)

### 1.1 Ragebait: full rewrite — from debate-finder to argument engineer
**Lever:** identity threat + engineered asymmetry (buyer on the winning side) + receipts.
**Where:** `chrgd/trends.py:RAGEBAIT_PROMPT` (lines 486-558). Drop-in: emits the exact same `MomentsResult` JSON shape — no model or parser change.

Replace the entire prompt string with:

```
You are the Argument Engineer for CHRGD, a premium UK gym/supplement brand.

Your job is NOT to find balanced debates. It is to ENGINEER provocations:
confident, slightly-overreaching claims that put a specific out-group on the
defensive and make them NEED to reply, while our own crowd cheers, shares and
piles in. On TikTok the comment war is the distribution engine — and the war
starts when someone's identity, effort or spending is threatened, never when a
familiar debate topic is politely raised.

WHO WE FIGHT FOR (the in-group — never the target): people who train seriously
and spend money doing it properly — lifters, early-session regulars, people who
buy protein/creatine/electrolytes/pre-workout and actually read the label.
Every take must land with THEM on the winning side, feeling seen and armed.
The rage comes from an OUT-GROUP: a rival tribe, a lazy habit, an overpriced
product category, or a myth-peddling school of thought. We never rage at our
own buyers.

Use web search for what gym-tok, fitness forums and current reporting are
actually arguing about right now — then ENGINEER each take with this formula:
1. TARGET an out-group behaviour / product category / myth / tribe
   (never an individual, never a brand by name, never a protected group).
2. CLAIM: one confident, slightly-overreaching position that invalidates
   something the out-group has spent money, effort or self-image on. Threat is
   the engine — use at least one lever per take:
   · sunk cost — "you've been paying £X a month for…"
   · effort invalidation — "your 2-hour session is a 40-minute workout with a
     podcast"
   · status inversion — "that's beginner behaviour dressed up as advanced"
   · price/markup exposure — "you're paying for the can, not the caffeine"
3. GAP: leave ONE obvious "well, actually" unaddressed — the opening the
   comments rush to fill. Name it explicitly.
4. RECEIPT: attach the single checkable fact/number we reply with when it
   kicks off. A claim with no receipt is a liability, not a take — cut it.

THE ASYMMETRY TEST (hard filter — this replaces any 50/50 instinct): a great
take is NOT evenly split. Roughly 60–80% of OUR audience should be cheering
and sharing ("finally someone said it"); the remaining out-group must be big
and invested enough to fight loudly in the comments. Apply ruthlessly:
- If nearly everyone would agree → crowd-pleaser, cut it.
- If the people it wounds are OUR OWN BUYERS → cut it, whatever the numbers.
- If it's a tired both-sides debate the internet has already had a thousand
  times (cardio vs weights, as a topic) → cut it, or sharpen it into a
  one-sided claim with a fresh threat lever.

RULES OF THE FIGHT (hard, non-negotiable — provocation, never harm):
- DEFENSIBLE ONLY: every claim must be a position we can back with its receipt
  when challenged — never a fabricated fact, invented statistic, or debunked
  myth presented as truth. The fight is over the take, not over whether we lied.
- Punch at BEHAVIOURS, PRODUCT CATEGORIES, MYTHS and TRIBES — never named
  people or brands, never protected groups, no body-shaming, no mocking anyone
  for being new. Mock choices people can laugh about defending, never who
  someone is.
- Claim safety as ever: nothing medical, no cure/treat/prevent or
  guaranteed-outcome language; supplement claims stay observational.
- The brand must be able to STAND BEHIND every word when it blows up.

Limitation you MUST respect: {limitation}

For EACH engineered argument, return 2-3 angles (take / ranking / callout),
each executing the full formula.

Return a SINGLE JSON object, no markdown, no commentary, in EXACTLY this shape:
{{
  "moments": [
    {{
      "title": "the CLAIM in one line at full confidence — the take itself, never the debate topic",
      "emoji": "one emoji",
      "category": "etiquette | training | supplements | diet | culture | cardio",
      "when": "live — arguing about it this week | evergreen beef",
      "why": "one line each side — 'in-group cheering: … / out-group raging: …'",
      "decay_speed": "days | weeks",
      "angles": [
        {{
          "type": "take | ranking | callout",
          "title": "short label",
          "hook": "the slide-1 claim, full confidence, threat lever visible in the words",
          "concept_note": "buildable brief ENDING with: 'the fight: <in-group> vs <out-group> over <what> · the gap: <the well-actually left open> · the receipt: <the fact we reply with>'"
        }}
      ]
    }}
  ]
}}
Rank by how hard the out-group will fight while our crowd cheers — the most
asymmetric, most-felt provocations first. Only takes that pass the asymmetry
test AND the rules of the fight.
```

**The bar — 5 example outputs this prompt should produce:**

1. *"Energy drinks are pre-workout for people who gave up."* — Lever: price/markup + status inversion. In-group: scoop users. Out-group: can-a-day crowd. Gap: sugar-free cans exist. Receipt: per-mg caffeine, a can costs ~4× a scoop serving.
2. *"If you 'don't believe in creatine' you don't believe in the most-studied supplement in existence — you believe in vibes."* — Lever: status inversion. In-group: creatine takers. Out-group: "it's just water weight" sceptics. Gap: non-responders are real. Receipt: hundreds of peer-reviewed studies; the most-researched ergogenic aid there is.
3. *"Fasted 6am training isn't discipline, it's underfuelled cosplay — you're proud of the alarm, not the session."* — Lever: effort invalidation. In-group: people who fuel before training (our buyer). Out-group: fasted-grind bros. Gap: fasted low-intensity cardio has legitimate uses. Receipt: performance drops measurably training glycogen-depleted.
4. *"'Just drink water' is hydration advice from people who've never sweated through a session."* — Lever: effort invalidation + myth attack. In-group: electrolyte users. Out-group: plain-water purists. Gap: "you get electrolytes from food." Receipt: sweat loses sodium; water alone replaces none of it.
5. *"Your BCAAs are a £30-a-month donation if you already eat protein."* — Lever: sunk cost. In-group: protein/creatine-first buyers. Out-group: stack maximalists. Gap: fasted-training edge cases. Receipt: adequate daily protein already covers leucine thresholds. (Note: attacks a category we don't lead with; positions the brand as "essentials done right".)

### 1.2 Trending: full rewrite — from "biggest trend" to trend hijacker
**Lever:** rising-not-peaked × niche × non-obvious angle, with the obvious take generated and rejected.
**Where:** `chrgd/trends.py:TRENDING_PROMPT` (lines 399-480). Drop-in same-schema (extra JSON keys are ignored by pydantic; `tier` rides the free-text `when` field so the UI shows it with zero model changes).

Replace with:

```
You are the Trend Hijacker for CHRGD, a premium UK gym/supplement brand.

CHRGD posts PHOTO CAROUSELS only — swipeable still images with text. Your job
is NOT to list the biggest trends. The biggest trend is the most saturated:
by the time everyone's on it, a small account posting the obvious take is
invisible. Your job is the INTERSECTION: a rising or under-exploited trend ×
CHRGD's niche (gym, energy, training culture) × an angle nobody else is
taking. Fresh-and-rising beats huge-and-covered, every time.

Use web search for what a UK 18-30, TikTok-native audience is participating in
right now (NOT news/fixtures/weather — another lane covers those). Hunt in two
buckets:
1. CAROUSEL-NATIVE formats — tier lists, "types of ___", hot takes, POV
   text-on-image, red/green flags, screenshot & notes-app posts, fake texts,
   photo dumps, expectation vs reality, "overheard at…".
2. ADAPTABLE video trends — a video format whose underlying idea/joke can be
   reframed into a swipe. Keep the idea, drop the motion, and say exactly how.

FOR EVERY CANDIDATE, CALL ITS TIER honestly:
- "emerging" — early signals, small creators, high upside.
- "rising" — clearly growing, not yet wall-to-wall. ← THE SWEET SPOT.
- "peaked" — everywhere this week. Only usable with a twist that reads as
  in-on-the-joke, not late.
- "saturated" — brands are on it. REJECT, unless the angle mocks the
  saturation itself (the only fresh move left).
Prefer rising > emerging > peaked-with-a-twist. Never return a saturated trend
played straight.

THE OBVIOUS-TAKE FIREWALL (hard, per trend): before writing any angle, state
the obvious take — the one every fitness account will post — and REJECT it.
Every angle you return must be a different move from the obvious take. The
bridge formula for each angle: trend format → a SPECIFIC lived moment from
CHRGD's world (a named gym situation, an exact ritual, a price, a time) → the
twist that makes it ours. Generic gym-ification of a trend fails the firewall.

THE CAROUSEL TEST (hard): could this land the same dopamine as still images
someone swipes through? If it needs motion, a transition, lip-sync or a
specific sound — and can't be reframed into stills — cut it.

THE FEED TEST (hard): would a UK 18-30 TikTok user recognise this from their
own feed or group chats THIS week? If only marketers and trend reports talk
about it, cut it. No quota: three real, rising, buildable trends beat six
padded ones.

Limitation you MUST respect: {limitation}
Because you cannot see the live For You feed: work from current reporting and
roundups, never claim a specific sound is trending, and describe each trend
precisely enough that the editor can verify it in the app in 30 seconds.

For EACH trend, return 2-3 CAROUSEL angles in CHRGD's world. Each angle's
concept_note MUST spell out the SWIPE SHAPE (slide 1 hook → middle beats →
final payoff/CTA) and MUST NOT be the obvious take.

Return a SINGLE JSON object, no markdown, no commentary, in EXACTLY this shape:
{{
  "moments": [
    {{
      "title": "the trend in one line, named the way people say it",
      "emoji": "one emoji",
      "category": "list | take | pov | screenshot | flags | aesthetic | gym | discourse",
      "scope": "global | uk",
      "when": "rising — growing, not saturated | emerging — early | peaked — needs the twist",
      "peak": "when to post to catch it",
      "carousel_fit": "native | adaptable",
      "obvious_take": "the take every fitness account will post — REJECTED",
      "why": "what participating signals + the evidence it's at this tier — and, if adaptable, how the video trend becomes a carousel",
      "decay_speed": "days | weeks",
      "angles": [
        {{
          "type": "advice | funny | tiein",
          "title": "short label",
          "hook": "the slide-1 hook this would open with",
          "concept_note": "the bridge (trend → our specific lived moment → the twist), then the swipe shape: slide 1 → middle beats → payoff/CTA"
        }}
      ]
    }}
  ]
}}
Rank by hijack potential: (tier: rising first) × (niche fit) × (freshness of
the angle vs the obvious take). Never by raw size.
```

**The bar — generic vs sharpened, side by side:**

| Trend | The obvious take (rejected) | The hijack |
|---|---|---|
| "What I'd never do as a ___" format | "5 things I'd never do as a personal trainer" | *"What I'd never do as the guy who's been at this gym 10 years"* — slide-per-sin of gym-floor politics (claiming the rack by leaving a towel, the 40-min phone set), payoff: the one thing he'll defend to the death. Insider status, tag-a-mate engine. |
| Notes-app screenshot confessions | "My honest supplement notes" | *A found notes-app list titled "lads I owe apologies to at PureGym"* — each entry one hyper-specific micro-crime ("the guy whose superset I walked through, twice"). Screenshot-native, travels to group chats whole. |
| "Underconsumed" / hidden-gem audio-less photo dumps | "Underrated supplements photo dump" | *"Underconsumed: things in your gym bag doing more than your pre-workout"* — dump ranks the unglamorous (sleep, the £4 chalk, the water bottle you actually finish) above the glamorous, ending on the one scoop that earns its place. Anti-hype = trust + saves. |

### 1.3 Model routing: creative model writes the claims
**Lever:** the sharpest-required creative is currently written by the cheapest model.
**Where:** `chrgd/trends.py:OpenAITrendClient.__init__` (125-137), `chrgd/worker.py:_handle_discover` (386-395).

```diff
 class OpenAITrendClient:
-    def __init__(self, settings: Settings):
+    def __init__(self, settings: Settings, model: str | None = None):
         ...
-        self._model = settings.scout_model
+        # Research lanes aggregate (cheap model is fine); claim-crafting lanes
+        # ship creative, so they may be routed to the creative model.
+        self._model = model or settings.scout_model
```

```diff
 def _handle_discover(store, settings, job):
     from .trends import scout_discover
+    from .trends import OpenAITrendClient
     params = json.loads(job["params_json"] or "{}")
-    result = scout_discover(settings, job["kind"], int(params.get("count", 6)))
+    # Ragebait and trending live or die on the sharpness of the written claim —
+    # give them the creative model; moments/evergreen stay on the cheap scout.
+    client = (
+        OpenAITrendClient(settings, model=settings.openai_model)
+        if job["kind"] in ("ragebait", "trending") else None
+    )
+    result = scout_discover(
+        settings, job["kind"], int(params.get("count", 6)), client=client
+    )
```
Cost: ~2 creative-model web-search completions/day at most (lane cadences: trending 24h, ragebait 3d). Trivial next to image spend.

### 1.4 Facts-door angles: takes-level rigor
**Where:** `chrgd/pipeline.py:ANGLES_CONTRACT` (849-876). Add per-angle requirements mirroring `TAKES_CONTRACT`: a named high-arousal `emotion`, a completed `share_identity`, and drop the "nobody tells you this" suggestion (it's on the engine's banned-hook list, `content_engine_prompt.md:64`) in favour of naming playbook shapes. Additive fields — `Angle` model gains `emotion`/`share_identity` defaults `""`.

### 1.5 Format gallery: purge the banned hooks
**Where:** `config/mechanics.toml`. `five_mistakes` slide 5 → `"The mistake that actually separates results + fix, then 'screenshot this before your next session'"` (27); rewrite `nobody_tells_you` into `insider_mechanism` — hook line `"The real reason <X> works has nothing to do with what you were told"` (63-72). Optionally add a `play = "save|share|comment"` field per mechanic so the gallery carries an engagement mechanic, not just structure.

### 1.6 Moments: demand the insider detail + the road less posted
**Where:** `chrgd/trends.py:MOMENTS_PROMPT` angle recipe (269-277). Append: *"Each angle must carry at least one insider-specific detail of the moment itself (the exact kick-off time, the 2am alarm, the named episode) — rounded generalities fail. And at least one angle per moment must be the take the OTHER brands won't post — everyone covers the moment; we cover the corner of it nobody else is standing in."*

### 1.7 Evergreen: share-identity per fact
**Where:** `chrgd/trends.py:EVERGREEN_PROMPT` quality bar (358-362). Add: *"— Sendable: complete 'sending this to a gym mate says ___ about me' for the fact; if the blank can't be filled, it's trivia, not content."*

---

## Phase 2 — Performance & fetch architecture

### 2.1 Split the worker: research lane + fast lane — **the structural change; plan first**
**Lever:** B1 (Critical). One thread means every scan blocks every other job.
**Proposal:** two `Worker` instances in the web app's lifespan — a *fast* worker excluding research kinds, and a *research* worker running only `{"moments","evergreen","trending","ragebait","moment_detail","trends","meta_scan"}`.
**Where:** `chrgd/worker.py:Worker` (gains `kinds: frozenset | None`), `chrgd/db.py:claim_next_job:480` (gains a kind filter **and must become an atomic claim** — `UPDATE jobs SET status='PROCESSING' WHERE job_id=? AND status='QUEUED'` + rowcount check, since two consumers can now race the same SELECT), `chrgd/webapp.py:create_app` lifespan (start/stop both).
**Tradeoffs (why this is safe but must be deliberate):** SQLite is already in WAL mode (ROADMAP A1) and every worker opens its own connection (`worker.py:39-42`), so concurrency is supported; the risks are (a) the claim race — fixed by the atomic UPDATE, and (b) two simultaneous OpenAI calls — fine. The "single worker" assumption is documented in `worker.py:6-9` and deploy docs; both get updated. **~40 lines. I'd present this diff for sign-off before implementing.**
This one change also fixes the `meta_scan` queue-cutting (`worker.py:211-213`) for free — research jobs can no longer delay a build/render.

### 2.2 Two-stage scans: headlines fast, angles on tap
**Lever:** B2. The user needs 6 headlines in ~10s, not 18 fully-written angles in 60s.
**How (phased, additive):**
1. Stage 1: `_DISCOVER_ASKS` gains a `headlines_only` variant — same prompts, but "return each moment with title/emoji/why/tier only, `angles: []`". Faster completion, same parser.
2. Stage 2: new job kind `lane_angles` — `POST /api/moments/{job_id}/angles {moment: i}` generates that one story's 2-3 angles with the **creative model** (the same pattern `moment_detail` dig already uses, `trends.py:680-698` / `webapp.py:api_moment_dig`). `angleChip` renders from it; tapping a story shows an inline angle-skeleton for ~8-15s instead of the whole lane blocking for a minute.
3. The old single-shot path stays as fallback (`lane_angles` failure → "rescan the lane").
This also *raises quality*: stage 2 spends its whole completion on one story's angles instead of splitting attention across eighteen.

### 2.3 Warm the radar before you arrive
**Lever:** B3 — the first open of the day always pays.
**Where:** `create.html` boot (2010-2050) + existing dedupe (`worker.py:enqueue_discover:497-508`).
On `/create` load (non-resume), fire one background `fetch('/api/moments?kind=…')` per lane and `POST /api/jobs/moments` for any lane that is empty/stale — exactly what `loadLane` does today, but for all four lanes at page-open, before you've picked a door. With 2.1 in place these queue on the research lane and never block a build. ~15 lines of JS; server dedupe already prevents stacking. (Optional later: a morning cron via the existing systemd timer pattern.)

### 2.4 Tell the truth about errors (+ working retry, no alert())
**Where:** `chrgd/webapp.py:api_moments`, `create.html:pollLane/loadLane/digMoment/post`.

```diff
 # webapp.py api_moments — after fetching `done`:
+        last = store.conn.execute(
+            "SELECT status, error FROM jobs WHERE kind = ? "
+            "ORDER BY job_id DESC LIMIT 1", (kind,),
+        ).fetchone()
+        payload["last_error"] = (
+            last["error"] if last and last["status"] == "ERROR" else None
+        )
```

```diff
 // create.html pollLane — the terminal branch:
-  if (data.moments.length) drawMoments(data);
-  else document.getElementById('radar-age').textContent = 'scan came back empty — try rescan';
+  if (data.moments.length) drawMoments(data);
+  else if (data.last_error) {
+    document.getElementById('radar-body').innerHTML =
+      `<div class="job-panel" style="border-color:#7a2c2c">❌ Scan failed:
+       ${esc(data.last_error)} <button class="chip" onclick="refreshLane(true)">↻ Try again</button></div>`;
+  }
+  else document.getElementById('radar-age').textContent = 'scan came back empty — try rescan';
```
Plus: `loadLane`'s `if (!r.ok) return;` → render the same error panel; the dig retry link (`create.html:1038`) → `onclick` actually re-calls `digMoment`; replace `alert()`/`fail()` (`create.html:1995-2008`) with a small inline toast (`.flash`-styled, auto-dismiss) — one helper, used by `post()` and the two validation alerts.

### 2.5 Perceived-performance polish
- Don't wipe a cached lane to skeletons on tab switch — only skeleton when there's nothing to show (`loadLane`, `create.html:883-884`: move the skeleton reset behind the fetch's empty check).
- Rescan chip gets an in-flight state (`disabled` + `↻` spin) so a tap visibly registers.
- Scan waits get the same elapsed + expectation copy the takes wait already has (`create.html:432` pattern → `radar-age`): "scanning… usually 30–60s".

---

## Phase 3 — UX & mobile-first

### 3.1 Source screen: three groups + a default, not eight equal doors
**Where:** `create.html:272-326`. Restructure (markup-only; all routes exist):
- **Hero card — "🎯 Find me today's post"** → opens the radar on the **last-used lane** (persist in `localStorage`, 3 lines in `switchLane`). The four radar doors collapse into this one card — they already share one screen.
- **"💡 I've got something"** → idea / facts (two compact cards).
- **"🧱 Structure first"** → format / manual (two compact cards, `optgrid`).
Result: the daily decision is one obvious tap; the wall becomes 5 cards in 3 tiers, and the journey drops a tap for the radar path.

### 3.2 Touch targets to 44px
**Where:** `create.html` CSS.
```diff
-  .chip { ... border-radius: 20px; padding: 7px 14px; font-size: 13px; ... }
+  .chip { ... border-radius: 22px; padding: 11px 16px; font-size: 13.5px;
+          min-height: 44px; display: inline-flex; align-items: center; ... }
-  .angle-actions button { padding: 7px 13px; font-size: 12.5px; }
+  .angle-actions button { padding: 11px 15px; font-size: 13px; min-height: 44px; }
-  .cj-back { ... padding: 2px 10px 4px; font-size: 20px; ... }
+  .cj-back { ... padding: 8px 14px; font-size: 22px; min-width: 44px; min-height: 44px; ... }
```

### 3.3 Surface what tooltips hide
- Format gallery: render the skeleton inline — first 2 beats + "… tap to see all 5" expanding on tap (`create.html:398-407`); kill the `title`-only skeleton.
- The 🎯/⚡ pair: one visible microcopy line under the first pair per screen — *"🎯 shows 5 directions first (recommended) · ⚡ writes immediately"* — instead of per-button `title`s.
- Length chips: append the slide counts to the labels ("Quick hit · 3–4") and fix the stale "1–2 slides" tooltip (`create.html:2019`) to match `pipeline.py:LENGTH_PREFS`.

### 3.4 Small polish
- Retire the legacy `/trends` link on the facts door (`create.html:385-386`) → "open the 📈 Trending lane" (routes to the radar); mark `trends.html` for deletion once nothing links to it.
- Bump `--faint` to ≥ #7e8894 for AA contrast at small sizes (`base.html:14`).
- Nav: keep the top bar, but on ≤600px give Create/Calendar a fixed bottom tab pair (thumb zone) — additive `@media` block in `base.html`; defer if it fights the header design.

---

## Sequencing

| Order | Change | Type | Risk |
|---|---|---|---|
| 1 | 1.1 + 1.2 prompt rewrites | prompt, drop-in | none (same schema) |
| 2 | 1.3 model routing | 10-line diff | trivial cost increase |
| 3 | 2.4 error truth + toast, 2.5 polish | small code | none |
| 4 | 1.4–1.7 lane/gallery hardening | prompt/config | none |
| 5 | 3.1–3.3 UX restructure | template-only | visual QA on phone |
| 6 | **2.1 worker split** | structural (~40 lines) | claim must go atomic — present diff first |
| 7 | 2.3 warm-on-open | 15-line JS | needs 2.1 |
| 8 | 2.2 two-stage scans | new job kind | medium; after 2.1 |

Measurable outcomes: radar first-paint on a warm cache < 1s with no skeleton flash; first-ever lane open ~10-15s to headlines (vs 30-90s to everything); no user job ever queued behind research; ragebait output that names the fight, the gap and the receipt on every angle; trending output that states the obvious take and refuses it.
