"""The story stage, the story gate, and the copy lint.

All three exist because of one real published slide:

    8PM: RACK'S GUEST LIST ONLY
    Velvet rope = lifting straps. Wonky '20:00' sign.

Three separate failures in one image — art direction printed as copy, no
character anywhere in frame, and a premise where a story should be. These tests
are what stop each of them coming back.
"""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.copylint import lint_post, reasons
from chrgd.db import Store
from chrgd.models import Idea
from chrgd.pipeline import build_user_message
from chrgd.shows import get_show
from chrgd.story import Story, StoryVerdict, gate_story, story_from_route, write_story


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db",
                    CHRGD_OUTPUT_DIR=tmp_path / "out")


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


GOOD_STORY = {
    "title": "BACK SOON",
    "logline": "Tracy Beaker wants nobody to know she fixed the lat pulldown.",
    "prose": "The sign had said BACK SOON since 2019. On Tuesday the machine "
             "worked. Orangina found her own laminated ingredients list wedged "
             "under the frame as a shim and accepted an ovation she had not "
             "earned. In the corner, Tracy Beaker had grease on both hands.",
    "change": "Tracy Beaker let Orangina take the credit, then broke it again.",
    "unresolved": "Tung Tung Tung Sahur saw her do it.",
    "cast": ["tracy_beaker", "orangina"],
}


GOOD_PREMISE = {
    "lead": "tracy_beaker",
    "flaw": "cannot be seen being kind",
    "trap": "she gets caught doing something nice",
    "self_inflicted": "she fixes it herself, at 3am, and leaves evidence",
    "reversal": "she has to hand the credit to someone who did nothing",
    "the_joke": "the funny is: she has to sabotage her own good deed to stay awful",
    "cost": "she loses the repair and watches Orangina take a bow for it",
}

PASS = {"is_a_story": True, "retell": "Tracy hid that she fixed it",
        "who_wanted_what": "Tracy wanted nobody to know", "character_count": 2,
        "funniest_line": "I have never fixed anything in my life",
        "who_lost_what": "she lost the credit, publicly"}


class FakeWriter:
    """Stands in for the writer. Serves the premise pitch first (the stage 0
    call), then a queued sequence of stories."""

    def __init__(self, *payloads):
        self._payloads = list(payloads)
        self.prompts: list[str] = []
        self.premise_prompts: list[str] = []

    def complete(self, system, user):
        if "pitch the COMIC IDEAS" in system:
            self.premise_prompts.append(user)
            payload = {"premises": [GOOD_PREMISE]}
        else:
            self.prompts.append(user)
            payload = self._payloads.pop(0) if self._payloads else GOOD_STORY

        class R:
            content = json.dumps(payload)
            prompt_tokens = completion_tokens = 10
        return R()


class FakeJudge:
    def __init__(self, *verdicts):
        self._verdicts = list(verdicts)
        self.seen: list[str] = []

    def judge(self, system, user):
        if "picking which ONE comic idea" in system:
            return json.dumps({"winner": 0, "why": "it lands instantly"})
        self.seen.append(user)
        v = self._verdicts.pop(0) if self._verdicts else PASS
        return json.dumps(v)


# --- the copy lint: art direction must never reach the artwork -------------


def test_the_lint_catches_the_slide_that_started_all_this():
    post = {"slides": [{
        "headline": "8pm: Rack's guest list only",
        "supporting": "Velvet rope = lifting straps. Wonky '20:00' sign.",
    }]}
    rules = {f.rule for f in lint_post(post)}
    assert "gloss_equals" in rules      # the '=' shorthand
    assert "prop_note" in rules         # the set-dressing note


@pytest.mark.parametrize("text,rule", [
    ("Velvet rope = lifting straps", "gloss_equals"),
    ("Tracy in the background, arms folded", "camera_language"),
    ("This panel shows the empty rack", "panel_talk"),
    ("Battered neon sign", "prop_note"),
    ("CUT TO: the car park", "art_direction_verbs"),
    ("Palette: #29C2F2 and black", "colour_notation"),
])
def test_every_leak_pattern_fires(text, rule):
    post = {"slides": [{"headline": "h", "supporting": text}]}
    assert rule in {f.rule for f in lint_post(post)}


@pytest.mark.parametrize("text", [
    "My name is Tracy Beaker and I have never fixed anything in my life.",
    "She has machine grease on both hands and an Allen key in her back pocket.",
    "The sign has said BACK SOON since 2019.",
    "Clarkson put down his coffee for the first time since March.",
    "But is it clean though.",
    "Nobody in Iron Palace has ever met Tracy Beaker's mum. Nobody says so.",
])
def test_the_lint_leaves_real_writing_alone(text):
    """A lint that fires on good copy is one the editor learns to ignore."""
    assert lint_post({"slides": [{"headline": text}]}) == []


def test_the_lint_reads_every_field_that_gets_painted():
    """headline, supporting AND body all end up on the artwork."""
    for field_name in ("headline", "supporting", "body"):
        post = {"slides": [{field_name: "rope = straps"}]}
        assert lint_post(post), field_name


def test_a_leak_becomes_a_rewrite_instruction():
    post = {"slides": [{"headline": "h", "supporting": "rope = straps"}]}
    notes = reasons(post)
    assert notes and "image brief" in notes[0]


def test_the_contract_now_defines_the_fields_that_get_painted():
    """The root cause: these were bare 'string' while image_prompt got nine
    lines, so the engine used supporting as a scratchpad."""
    from chrgd.pipeline import JSON_CONTRACT

    assert '"headline": "string"' not in JSON_CONTRACT
    assert '"supporting": "string"' not in JSON_CONTRACT
    assert "velvet rope = lifting straps" in JSON_CONTRACT.lower()
    assert "NOT a notes field" in JSON_CONTRACT


# --- the story stage --------------------------------------------------------


def test_the_story_is_written_before_any_slides(store, settings):
    idea = Idea(idea_id="G1", concept_note="the lat pulldown gets fixed")
    writer, judge = FakeWriter(GOOD_STORY), FakeJudge()

    story, spend = write_story(idea, settings, store, cast_keys=["tracy_beaker"],
                               client=writer, judge=judge)
    assert story is not None and story.is_usable()
    assert story.title == "BACK SOON"
    assert spend > 0
    # it was asked for prose, and it was given the world and the cast
    assert "Iron Palace" in writer.prompts[0]
    assert "Tracy Beaker" in writer.prompts[0]


def test_the_editors_premise_is_treated_as_the_direction(store, settings):
    """Option B: one line from the operator drives the whole episode."""
    idea = Idea(idea_id="G1",
                concept_note="Tracy Beaker fixes the lat pulldown and won't admit it")
    writer = FakeWriter(GOOD_STORY)
    write_story(idea, settings, store, cast_keys=["tracy_beaker"],
                client=writer, judge=FakeJudge())
    assert "EDITOR'S PREMISE" in writer.prompts[0]
    assert "won't admit it" in writer.prompts[0]


def test_a_placeholder_premise_is_not_treated_as_direction(store, settings):
    idea = Idea(idea_id="G1", concept_note="the next episode")
    writer = FakeWriter(GOOD_STORY)
    write_story(idea, settings, store, cast_keys=["tracy_beaker"],
                client=writer, judge=FakeJudge())
    assert "EDITOR'S PREMISE" not in writer.prompts[0]


# --- the story gate: a premise must not get through ------------------------


def test_a_premise_fails_the_cold_read_and_is_rewritten(store, settings):
    """The exact failure: '8pm: rack's guest list only' is a situation."""
    premise = {**GOOD_STORY, "title": "Guest List",
               "prose": "The squat rack now has a guest list. It is very "
                        "exclusive. There is a velvet rope."}
    writer = FakeWriter(premise, GOOD_STORY)
    judge = FakeJudge(
        {"is_a_story": False, "retell": "nothing — it describes a rack",
         "who_wanted_what": "nobody", "what_it_cost": "nothing",
         "verdict": "this is a situation, not a story"},
        PASS,
    )
    story, _spend = write_story(Idea(idea_id="G1", concept_note=""), settings,
                                store, cast_keys=["tracy_beaker"],
                                client=writer, judge=judge)
    assert story.title == "BACK SOON"          # the rewrite, not the premise
    assert len(writer.prompts) == 2
    assert "FAILED A COLD READ" in writer.prompts[1]
    assert "situation, not a story" in writer.prompts[1]


def test_the_gate_reads_only_the_prose(store, settings):
    """Cold means cold — it must not be shown the canon or the brief, or it
    will infer a story that isn't on the page."""
    judge = FakeJudge({"is_a_story": True})
    gate_story(Story(**GOOD_STORY), settings, judge=judge)
    assert judge.seen[0].strip() == GOOD_STORY["prose"]
    assert "Iron Palace" not in judge.seen[0]


def test_the_rewrite_is_bounded(store, settings):
    """If the second attempt still isn't a story the premise is the problem,
    and a third go just spends money agreeing with itself."""
    writer = FakeWriter(GOOD_STORY, GOOD_STORY, GOOD_STORY)
    judge = FakeJudge({"is_a_story": False, "verdict": "no"},
                      {"is_a_story": False, "verdict": "still no"},
                      {"is_a_story": False, "verdict": "no"})
    story, _ = write_story(Idea(idea_id="G1", concept_note=""), settings, store,
                           cast_keys=["tracy_beaker"], client=writer, judge=judge)
    assert len(writer.prompts) == 2      # one write + one rewrite, then stop
    assert story is not None             # and we keep the best we have


def test_a_broken_gate_never_blocks_the_story(store, settings):
    class Exploding:
        def judge(self, system, user):
            raise RuntimeError("api down")

    story, _ = write_story(Idea(idea_id="G1", concept_note=""), settings, store,
                           cast_keys=["tracy_beaker"],
                           client=FakeWriter(GOOD_STORY), judge=Exploding())
    assert story is not None and story.is_usable()


def test_a_broken_writer_falls_through_to_the_old_behaviour(store, settings):
    class Exploding:
        def complete(self, system, user):
            raise RuntimeError("api down")

    story, spend = write_story(Idea(idea_id="G1", concept_note=""), settings,
                               store, cast_keys=["tracy_beaker"],
                               client=Exploding(), judge=FakeJudge())
    assert story is None and spend == 0.0


# --- the second call cuts, it does not invent ------------------------------


def test_the_build_is_told_to_cut_the_story_not_write_one():
    idea = Idea(idea_id="G1", concept_note="x", route_json=json.dumps({
        "show": "multiverse", "cast": ["tracy_beaker"], "story": GOOD_STORY,
    }))
    msg = build_user_message(idea)
    assert "THIS EPISODE IS ALREADY WRITTEN" in msg
    assert "CUT it into slides, not to invent a new one" in msg
    assert GOOD_STORY["prose"] in msg
    assert "Left deliberately open".upper() in msg.upper()
    assert "Keep the dialogue" in msg


def test_no_story_means_the_brief_is_unchanged():
    """The null case — an episode built before this stage existed."""
    idea = Idea(idea_id="G1", concept_note="x", route_json=json.dumps({
        "show": "multiverse", "cast": ["tracy_beaker"],
    }))
    msg = build_user_message(idea)
    assert "ALREADY WRITTEN" not in msg


def test_story_from_route_rejects_rubbish():
    assert story_from_route(None) is None
    assert story_from_route({"story": "not a dict"}) is None
    assert story_from_route({"story": {"prose": "", "change": ""}}) is None
    assert story_from_route({"story": GOOD_STORY}).title == "BACK SOON"


# --- somebody is in every panel --------------------------------------------


def test_the_show_demands_a_character_in_every_slide():
    voice = get_show("multiverse").voice.block
    assert "SOMEBODY IS IN EVERY PANEL" in voice
    assert "feature_character` true on every slide" in voice
    assert "SLIDE 1 OPENS ON A PERSON" in voice
    assert "empty room" in voice


def test_slide_one_opens_on_a_person_not_the_building():
    first = get_show("multiverse").spine.briefs[0]
    assert "LEAD CHARACTER" in first
    assert "Not the building" in first


def test_the_show_bans_what_actually_went_wrong():
    banned = " ".join(get_show("multiverse").voice.banned).lower()
    assert "no character visible" in banned
    assert "establishing shot" in banned
    assert "art direction, prop notes or set dressing written in the on-slide copy" in banned


def test_the_copy_rule_names_the_real_example():
    """The brief cites the actual failed slide, because a concrete example of a
    mistake is worth more than an abstract prohibition."""
    assert "velvet rope = lifting straps" in get_show("multiverse").voice.block


# --- the whole loop, through the real build path ---------------------------


def test_the_two_stage_build_end_to_end(store, settings, monkeypatch):
    """Story written → cut into slides → art-direction leak rewritten →
    prose persisted for the editor to read before any render."""
    from chrgd import claims as claims_mod
    from chrgd import pipeline

    qa = {k: 10 for k in (
        "hook", "swipe_loop", "identity_recognition", "group_chat_share",
        "comment_fight", "saveability", "visual_originality",
        "dopamine_density", "clarity", "layout_safety", "claim_safety",
        "overall")}
    seen: dict[int, str] = {}

    class Client:
        def __init__(self):
            self.n = 0

        def complete(self, system, user):
            self.n += 1
            seen[self.n] = user
            if "pitch the COMIC IDEAS" in system:
                return pipeline.LLMResult(
                    content=json.dumps({"premises": [GOOD_PREMISE]}),
                    prompt_tokens=1, completion_tokens=1)
            if "PROSE" in system:
                return pipeline.LLMResult(content=json.dumps(GOOD_STORY),
                                          prompt_tokens=1, completion_tokens=1)
            leaking = self.n <= 3      # first cut smuggles art direction in
            post = {
                "post_type": "carousel", "hook": "h",
                "hook_options": ["a", "b", "c"],
                "slides": [{
                    "headline": "My name is Tracy Beaker.",
                    "supporting": ("Velvet rope = lifting straps." if leaking
                                   else "She has grease on both hands."),
                    "image_prompt": "Tracy Beaker at the rack",
                    "visual_intent": "v", "role": "hook", "swipe_trigger": "t",
                    "feature_character": True}],
                "caption": "c", "comment_trigger": "who saw?",
                "hashtags": ["#gym"] * 6,
                "route": {"mechanic": "m", "visual_engine": "v",
                          "primary_goal": "g", "qa": qa},
            }
            return pipeline.LLMResult(content=json.dumps(post),
                                      prompt_tokens=1, completion_tokens=1)

    class Judge:
        def judge(self, system, user):
            if "picking which ONE comic idea" in system:
                return json.dumps({"winner": 0, "why": "it lands"})
            if "is_a_story" in system:
                return json.dumps(PASS)
            return json.dumps({"safe": True, "flags": []})

    monkeypatch.setattr(claims_mod, "OpenAIClaimsJudge", lambda s: Judge())
    store.add_idea(Idea(
        idea_id="G1",
        concept_note="Tracy Beaker fixes the lat pulldown and won't admit it",
        route_json=json.dumps({"show": "multiverse",
                               "cast": ["tracy_beaker", "orangina"]})))

    client = Client()
    result = pipeline.build_single_idea(store, settings, "G1", client=client,
                                        record_run=False)

    # stage 0 pitched comic ideas from the editor's direction
    assert "EDITOR'S DIRECTION" in seen[1] and "won't admit it" in seen[1]
    # stage 1 wrote prose against the chosen joke
    assert "THE COMIC IDEA" in seen[2]
    assert "sabotage her own good deed" in seen[2]
    # stage 2 was told to cut, not invent, and carried the story
    assert "THIS EPISODE IS ALREADY WRITTEN" in seen[3]
    assert "grease on both hands" in seen[3]
    # the leak earned a rewrite rather than shipping
    assert client.n >= 4 and "image brief" in seen[4]
    saved = store.get_idea("G1")
    assert "=" not in json.loads(saved.slides_json)[0]["supporting"]
    # and the prose is on the idea, for the editor to read before rendering
    assert json.loads(saved.route_json)["story"]["title"] == "BACK SOON"
    assert result.status.value == "done"


def test_the_prose_is_surfaced_to_the_editor(store, settings):
    """The cheapest quality gate in the pipeline: read the story before paying
    for eight images."""
    from fastapi.testclient import TestClient

    from chrgd.webapp import create_app

    st = Settings(CHRGD_DB_PATH=settings.db_path,
                  CHRGD_OUTPUT_DIR=settings.output_dir,
                  CHRGD_WEB_USERNAME="a", CHRGD_WEB_PASSWORD="b",
                  CHRGD_SECRET_KEY="k" * 32)
    store.add_idea(Idea(idea_id="G1", concept_note="x",
                        route_json=json.dumps({"show": "multiverse",
                                               "story": GOOD_STORY})))
    client = TestClient(create_app(st))
    client.post("/login", data={"username": "a", "password": "b"},
                follow_redirects=False)

    detail = client.get("/api/ideas/G1/detail").json()
    assert detail["story"]["prose"] == GOOD_STORY["prose"]
    assert 'id="write-story"' in client.get("/create").text


def test_a_non_serial_build_skips_the_story_stage(store, settings):
    """The null case: only a show with a cast pays for the extra call."""
    from chrgd import pipeline

    qa = {k: 10 for k in (
        "hook", "swipe_loop", "identity_recognition", "group_chat_share",
        "comment_fight", "saveability", "visual_originality",
        "dopamine_density", "clarity", "layout_safety", "claim_safety",
        "overall")}
    calls = {"n": 0}

    class Client:
        def complete(self, system, user):
            calls["n"] += 1
            assert "PROSE" not in system, "no story stage off the serial"
            assert "COMIC IDEAS" not in system, "no premise stage off the serial"
            return pipeline.LLMResult(content=json.dumps({
                "post_type": "carousel", "hook": "h",
                "hook_options": ["a", "b", "c"],
                "slides": [{"headline": "Amp forgot his trainers",
                            "supporting": "Again.", "image_prompt": "p",
                            "visual_intent": "v", "role": "hook",
                            "swipe_trigger": "t"}],
                "caption": "c", "comment_trigger": "which one are you?",
                "hashtags": ["#gym"] * 6,
                "route": {"mechanic": "m", "visual_engine": "v",
                          "primary_goal": "g", "qa": qa},
            }), prompt_tokens=1, completion_tokens=1)

    store.add_idea(Idea(idea_id="G1", concept_note="x",
                        route_json=json.dumps({"show": "amp"})))
    pipeline.build_single_idea(store, settings, "G1", client=Client(),
                               record_run=False)
    assert calls["n"] == 1
    assert "story" not in json.loads(store.get_idea("G1").route_json)


# --- clarity: the failures from "Six O'Clock Means War" --------------------


REAL_FAILURE = (
    "Ballerina Cappuccina pirouetted, taping bunting to the uprights, a "
    "clipboard like a crown, while slapping scoops into open palms and "
    "explaining that the vote would happen at six regardless. She climbed a "
    "plate stack. Choice time. The clipboard bent like a soft apology."
)


def test_the_prose_lint_catches_the_real_failure():
    from chrgd.story import lint_prose

    notes = " ".join(lint_prose(REAL_FAILURE))
    assert "simile" in notes
    assert "structural label" in notes
    assert "28 words" in notes


def test_the_prose_lint_leaves_plain_writing_alone():
    """The worked example must pass the lint it teaches."""
    from chrgd.story import lint_prose

    assert lint_prose(GOOD_STORY["prose"]) == []
    assert lint_prose(
        "On Tuesday morning it worked. Nobody argued. She made a short speech."
    ) == []


def test_the_story_prompt_bans_what_actually_went_wrong():
    from chrgd.story import STORY_SYSTEM

    for rule in ("EASY TO FOLLOW", "TWO CHARACTERS", "ONE THING PER SENTENCE",
                 "NO SIMILES AND NO METAPHORS", "ESTABLISH BEFORE YOU USE",
                 "CATCHPHRASES ARE A TOOL, NOT A TAX",
                 "NEVER WRITE A STRUCTURAL LABEL"):
        assert rule in STORY_SYSTEM, rule
    # it cites the actual failed lines, not abstract prohibitions
    assert "a clipboard like a crown" in STORY_SYSTEM
    assert "Choice time" in STORY_SYSTEM
    assert "the 4am footage" in STORY_SYSTEM


def test_the_gate_is_a_comprehension_test_not_a_shape_test():
    from chrgd.story import GATE_SYSTEM

    assert "COMPREHENSION test" in GATE_SYSTEM
    assert "RETELL IT" in GATE_SYSTEM
    assert "LIST EVERYTHING YOU HAD TO GUESS AT" in GATE_SYSTEM
    assert "re-read" in GATE_SYSTEM


def test_unexplained_references_fail_even_when_it_looks_like_a_story():
    """The exact hole: the old gate passed an episode nobody could follow,
    because 'is this a story' can be pattern-matched onto anything."""
    v = StoryVerdict(is_a_story=True, retell="they argued about a rack",
                     had_to_guess=["the vote", "the 4am footage"])
    assert not v.passed()
    assert "the vote" in v.failure_note()
    assert "never explained on the page" in v.failure_note()


def test_too_many_characters_fails():
    v = StoryVerdict(is_a_story=True, retell="four people did things",
                     character_count=4)
    assert not v.passed()
    assert "Cut it to TWO" in v.failure_note()


def test_a_line_that_needed_re_reading_fails():
    v = StoryVerdict(is_a_story=True, retell="ok",
                     confusing_lines=["the rack will decide who couples"])
    assert not v.passed()
    assert "re-read" in v.failure_note()


def test_a_clean_verdict_passes():
    assert StoryVerdict(**PASS).passed()


def test_dense_prose_is_rewritten_even_when_the_judge_is_happy(store, settings):
    """The lint is deterministic, so over-decorated writing earns the rewrite
    every time rather than whenever a judge happens to mention it."""
    dense = {**GOOD_STORY, "prose": REAL_FAILURE}
    writer = FakeWriter(dense, GOOD_STORY)
    judge = FakeJudge(PASS, PASS)

    story, _ = write_story(Idea(idea_id="G1", concept_note=""), settings, store,
                           cast_keys=["tracy_beaker"], client=writer, judge=judge)
    assert len(writer.prompts) == 2                    # it was sent back
    assert "simile" in writer.prompts[1]
    assert story.prose == GOOD_STORY["prose"]          # and the clean one won


def test_the_worked_example_models_the_plainness_it_teaches():
    from chrgd.roster import load_example
    from chrgd.story import lint_prose

    example = load_example()
    assert "Match how PLAIN it is" in example
    assert "absence of similes" in example
    # the example's own prose must survive the lint it exists to teach
    body = example[example.index("> The lat pulldown"):example.index("**Cast:**")]
    prose = "\n".join(line.lstrip("> ").strip() for line in body.splitlines()
                       if line.strip().startswith(">"))
    assert lint_prose(prose) == []


# --- the joke: the stage both failures skipped -----------------------------


def test_a_premise_needs_a_named_joke_and_self_infliction():
    """'First names on the board' had a want, an obstacle and a choice — and
    no comic idea underneath, because nothing ever asked for one."""
    from chrgd.story import Premise

    assert Premise(**GOOD_PREMISE).is_usable()
    assert not Premise(lead="x", flaw="y", trap="z").is_usable()
    assert not Premise(the_joke="the funny is: ...").is_usable()  # no self-infliction


def test_the_premise_prompt_teaches_the_method():
    from chrgd.story import PREMISE_SYSTEM

    for rule in ("PICK ONE CHARACTER", "BUILD THE TRAP",
                 "MAKE THEM SPRING IT THEMSELVES", "FIND THE REVERSAL",
                 "NAME THE JOKE IN ONE SENTENCE", "NAME THE COST"):
        assert rule in PREMISE_SYSTEM, rule
    assert "Bad luck is not comedy" in PREMISE_SYSTEM


def test_the_pitch_is_run_before_a_word_is_written(store, settings):
    writer, judge = FakeWriter(GOOD_STORY), FakeJudge()
    story, _ = write_story(Idea(idea_id="G1", concept_note=""), settings, store,
                           cast_keys=["tracy_beaker"], client=writer, judge=judge)
    assert writer.premise_prompts, "no premises were pitched"
    # the writer was handed the chosen joke, not left to find one
    assert "THE COMIC IDEA" in writer.prompts[0]
    assert GOOD_PREMISE["the_joke"] in writer.prompts[0]
    # and the joke rides on the story so the editor can see it
    assert story.the_joke == GOOD_PREMISE["the_joke"]


def test_the_process_document_reaches_the_pitch(store, settings):
    writer = FakeWriter(GOOD_STORY)
    write_story(Idea(idea_id="G1", concept_note=""), settings, store,
                cast_keys=["tracy_beaker"], client=writer, judge=FakeJudge())
    pitch = writer.premise_prompts[0]
    assert "THE PROCESS FOR FINDING THE JOKE" in pitch
    assert "name the flaw" in pitch.lower()
    assert "First names on the board" in pitch     # the failure, worked through


def test_the_funniest_premise_is_picked(settings):
    from chrgd.story import Premise, pick_premise

    premises = [Premise(**{**GOOD_PREMISE, "the_joke": f"joke {i}"})
                for i in range(3)]

    class Picker:
        def judge(self, system, user):
            assert "picking which ONE comic idea" in system
            return json.dumps({"winner": 2, "why": "it lands instantly"})

    chosen, why, _ = pick_premise(premises, settings, judge=Picker())
    assert chosen.the_joke == "joke 2"
    assert why == "it lands instantly"


def test_a_bogus_winner_index_falls_back_rather_than_crashing(settings):
    from chrgd.story import Premise, pick_premise

    premises = [Premise(**GOOD_PREMISE), Premise(**GOOD_PREMISE)]

    class Wrong:
        def judge(self, system, user):
            return json.dumps({"winner": 99})

    chosen, _why, _spend = pick_premise(premises, settings, judge=Wrong())
    assert chosen is premises[0]


def test_a_failed_pitch_still_produces_an_episode(store, settings):
    """The premise stage is a booster, not a dependency."""
    class NoPremises:
        def __init__(self):
            self.prompts = []

        def complete(self, system, user):
            if "pitch the COMIC IDEAS" in system:
                raise RuntimeError("api hiccup")
            self.prompts.append(user)

            class R:
                content = json.dumps(GOOD_STORY)
                prompt_tokens = completion_tokens = 5
            return R()

    writer = NoPremises()
    story, _ = write_story(Idea(idea_id="G1", concept_note=""), settings, store,
                           cast_keys=["tracy_beaker"], client=writer,
                           judge=FakeJudge())
    assert story is not None and story.is_usable()
    assert "THE COMIC IDEA" not in writer.prompts[0]


def test_the_story_prompt_demands_the_joke_land():
    from chrgd.story import STORY_SYSTEM

    assert "YOU HAVE BEEN GIVEN THE JOKE" in STORY_SYSTEM
    assert "CLEAR IS THE DELIVERY, FUNNY IS THE JOB" in STORY_SYSTEM
    assert "THE LAST LINE IS THE PUNCHLINE" in STORY_SYSTEM
    assert "Escalate in THREES" in STORY_SYSTEM or "escalate in THREES" in STORY_SYSTEM
    assert "SOMEBODY LOSES SOMETHING" in STORY_SYSTEM
    # it names the exact failure it exists to prevent
    assert "stood still" in STORY_SYSTEM


def test_a_clear_story_with_no_joke_in_it_fails():
    """'First names on the board': readable, well-formed, and pointless."""
    v = StoryVerdict(is_a_story=True,
                     retell="a man wanted a treadmill and got signed up for squats",
                     who_wanted_what="Tralalero wanted the treadmill",
                     character_count=3, funniest_line="", who_lost_what="")
    assert not v.passed()
    note = v.failure_note()
    assert "THERE IS NO JOKE IN IT" in note
    assert "Nobody lost anything" in note


def test_an_episode_that_trails_off_fails():
    v = StoryVerdict(**{**PASS, "ends_on_its_best_line": False})
    assert "trails off" in v.failure_note()


def test_the_joke_is_protected_when_the_story_is_cut_into_slides():
    story = Story(**{**GOOD_STORY, "the_joke": "the funny is: she sabotages herself"})
    brief = story.as_brief()
    assert "THE JOKE this episode is landing" in brief
    assert "must not be softened" in brief
