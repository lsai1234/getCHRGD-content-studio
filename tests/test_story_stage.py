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


class FakeWriter:
    """Returns a queued sequence of stories, recording what it was asked."""

    def __init__(self, *payloads):
        self._payloads = list(payloads)
        self.prompts: list[str] = []

    def complete(self, system, user):
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
        self.seen.append(user)
        v = self._verdicts.pop(0) if self._verdicts else {"is_a_story": True}
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
        {"is_a_story": False, "what_happened": "nothing — it describes a rack",
         "who_wanted_what": "nobody", "why_read_on": "no question",
         "what_it_cost": "nothing",
         "verdict": "this is a situation, not a story"},
        {"is_a_story": True, "what_happened": "Tracy Beaker hid that she fixed it"},
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
            if "PROSE" in system:
                return pipeline.LLMResult(content=json.dumps(GOOD_STORY),
                                          prompt_tokens=1, completion_tokens=1)
            leaking = self.n <= 2      # first cut smuggles art direction in
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
            if "is_a_story" in system:
                return json.dumps({"is_a_story": True,
                                   "what_happened": "Tracy hid that she fixed it"})
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

    # stage 1 wrote prose from the editor's premise
    assert "EDITOR'S PREMISE" in seen[1] and "won't admit it" in seen[1]
    # stage 2 was told to cut, not invent, and carried the story
    assert "THIS EPISODE IS ALREADY WRITTEN" in seen[2]
    assert "grease on both hands" in seen[2]
    # the leak earned a rewrite rather than shipping
    assert client.n >= 3 and "image brief" in seen[3]
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
