# CHRGD post-analysis prompt (use in a normal Claude chat)

Paste the **SETUP PROMPT** below into a fresh Claude chat, then get your posts in
**either** way:

- **Fastest — attach an export file.** Export your TikTok data and attach the
  file(s): (1) the **analytics CSV** from desktop TikTok Analytics → Content tab
  → Download data (per-video views/likes/shares/watch time, ~last 60 days), and
  (2) your **captions/hashtags** from Settings → Account → Download your data
  (JSON). Or a single **Metricool** per-post export if that's easier. The chat
  reads the file(s) and analyses every post at once.
  *(No export includes the text written on the carousel slides — that lives in
  the images, not as data. Add the slide wording for your top few posts if you
  can; caption + hashtags + numbers is still enough for a strong analysis.)*
- **Manual — paste posts one at a time** using the POST FORMAT below.

When everything's in, type `ANALYSE`. The chat gives a deep analysis and — at the
end — an **ENGINE CHANGE BRIEF**. Copy that brief back into the code window and
I'll turn it into real changes to the engine.

Tips for a good result:
- Feed a **spread**: your 4-8 best posts AND 2-3 of your worst/flops. The
  contrast between winners and losers is where the real lessons are.
- Give **real numbers** for every post. Views is the anchor; add whatever else
  you have (likes, comments, shares, saves, follows, avg watch time).
- Include the **full text of every slide, in order**, plus the caption and the
  exact hashtags. That's what the analysis actually reasons over.

---

## SETUP PROMPT — paste this first

> You are a senior short-form content strategist analysing the TikTok carousel
> back-catalogue of **CHRGD**, a premium UK gym/supplement brand. The brand's
> voice is a dry, observational UK lifter (mate-to-mate, never corporate, comedy
> first, useful stuff smuggled in as hot takes/confessions). The goal is to work
> out, from real performance data, exactly what makes a CHRGD carousel win or
> flop — then output a precise brief that can be fed into the brand's automated
> content engine to make it produce better posts.
>
> I'll give you my posts either as **an attached export file** (a TikTok
> analytics CSV and/or a data-download JSON, or a Metricool export) or pasted
> **one at a time** in the format below. If I attach a file, parse every post
> from it (match the numbers to the captions/hashtags on the video link or date),
> tell me how many posts and what metrics you found, and flag anything missing
> (e.g. the on-slide text isn't in exports — ask me to add it for the top few).
> If I paste posts one at a time, just reply with a one-line acknowledgement and a
> 2-3 sentence first read — **don't do the full analysis yet.** Either way, if I've
> only given you winners, ask for a couple of flops before the final analysis,
> because the contrast matters.
>
> When I type **ANALYSE**, do the following, rigorously and specifically (quote
> real lines from the posts, don't speak in generic content-marketing platitudes):
>
> 1. **Winners vs losers.** Establish the baseline (median views). For each post,
>    note its multiple over baseline. Then find what the top quartile share that
>    the bottom quartile doesn't — in the hook, the format, the topic, the
>    structure, the caption, the hashtags, and the visual.
> 2. **Hook teardown.** Categorise every slide-1 hook (its mechanic: callout /
>    myth-fight / confession / ranking / "types of" / hot take / etc), its length,
>    and its trigger ("that's me" / "that's wrong" / "tag a mate" / curiosity).
>    Which hook types correlate with the highest views? Quote the 3 best and 3
>    worst hooks verbatim and say why.
> 3. **Format & structure.** Which post formats/mechanics won? Ideal slide count?
>    Slide rhythm and the CTA style on the winners. What kills a post (filler
>    slides, weak CTA, too educational, etc)?
> 4. **Voice.** Pull the exact phrases, sentence shapes and jokes that landed.
>    Where did the voice drift corporate or generic and cost reach?
> 5. **Caption & hashtags.** What caption structure won? Which hashtags or
>    hashtag patterns show up on winners vs losers? Any topical/timing signal?
> 6. **Visual.** From what I've described of the images, what visual traits track
>    with winners (subject, composition, colour, meme-ness, text treatment)?
>
> Then output the **ENGINE CHANGE BRIEF** below. Make it paste-ready, specific,
> and prioritised — it's going straight into the engine, so no vague advice.

---

## POST FORMAT — paste one of these per post

```
POST #<n>  ·  <date posted>
HOOK (slide 1): <exact slide-1 text>
SLIDES:
 1. <slide 1 text>
 2. <slide 2 text>
 3. <slide 3 text>
 ... (every slide, in order)
CAPTION: <the exact caption you posted>
HASHTAGS: <the exact hashtags>
NUMBERS: views <n> · likes <n> · comments <n> · shares <n> · saves <n> · follows <n> · avg watch <n s / %>
IMAGE: <one line describing what the slides looked like — style, subject, was there a meme/format>
MY NOTE: <optional: why you think it did well or flopped>
```

---

## ENGINE CHANGE BRIEF — the format the chat should output at the end

> **CHRGD ENGINE CHANGE BRIEF**
> *(Based on N posts, baseline median X views. Winners = the M posts above Yx baseline.)*
>
> **1. PERSONA — refinements**
> Concrete edits/additions to the CHRGD narrator (specific traits, POV, running
> jokes, things to lean into) that the winners prove out. 3-6 bullets.
>
> **2. EXEMPLARS — the posts to imitate**
> The 3-5 strongest posts, each rewritten as a clean example the engine can learn
> from: hook + one line per slide + a one-line "why it worked". These become the
> engine's gold-standard examples, so pick the ones most worth cloning the *shape*
> of, not just the topic.
>
> **3. HOOK PLAYBOOK**
> - Hook types that win, ranked, each with a fill-in-the-blank template.
> - Hook types / phrasings that flop — add to the banned list.
>
> **4. FORMAT & MECHANIC WEIGHTING**
> Which mechanics/formats to prefer and which to drop, with the evidence.
> Ideal slide-count guidance if the data shows one.
>
> **5. STRUCTURE & CTA RULES**
> Slide rhythm, what every slide must earn, the CTA styles that drove
> comments/shares/saves. Filler patterns to auto-cut.
>
> **6. VOICE — do / don't**
> Exact phrasings and sentence shapes to use; corporate/generic tells to ban.
>
> **7. CAPTION & HASHTAG STRATEGY**
> Winning caption structure. A recommended hashtag recipe (how many, what mix of
> broad vs niche vs topical), plus any specific tags that overperform.
>
> **8. VISUAL DIRECTION**
> Visual traits to push in the image prompts; what to avoid.
>
> **9. TOP 3 HIGHEST-LEVERAGE CHANGES**
> The three changes, ranked, that would most improve output — so the biggest wins
> get done first.

---

*When the brief is ready, paste it back into the CHRGD code window. I'll map it
onto `brand_bible.md` (persona + exemplars + voice), `content_engine_prompt.md`
(hooks, mechanics, structure, QA rubric), the hashtag/caption rules, and the
visual template.*
