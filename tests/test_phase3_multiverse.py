"""Phase 3 — THE MULTIVERSE: the roster, the canon, and the likeness gate.

The likeness rules are the reason most of this file exists. They are the only
part of the whole plan with real outside risk, and rules that live in a prompt
hold right up until the week someone edits the prompt — so they're checked
against the finished episode, and these tests are what stop them quietly
rotting.
"""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.likeness import check_likeness, is_blocking, lint_post, named_characters
from chrgd.models import Idea, Status
from chrgd.pipeline import build_user_message
from chrgd.roster import (
    PROTECTED,
    cast_block,
    get_character,
    load_roster,
    load_world,
    resolve,
    visual_block,
)
from chrgd.series import (
    Canon,
    Episode,
    load_canon,
    record_episode,
    reset_canon,
    save_canon,
)


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "t.db",
                    CHRGD_OUTPUT_DIR=tmp_path / "out")


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def idea(**route) -> Idea:
    return Idea(idea_id="G1", concept_note="x",
                route_json=json.dumps(route) if route else None)


def episode_post(*copy, art="a comic panel in a gym", caption="what happens next?"):
    return {
        "slides": [{"headline": c, "supporting": "", "body": "",
                    "image_prompt": art, "visual_intent": ""} for c in copy],
        "caption": caption,
        "comment_trigger": "who wins?",
    }


# --- the roster -------------------------------------------------------------


def test_the_roster_is_the_thirteen_that_were_agreed():
    roster = load_roster()
    assert len(roster) == 13
    protected = [c for c in roster.values() if c.protected]
    assert len(protected) == 6, "six guest stars, seven meme regulars"
    names = {c.name for c in roster.values()}
    assert {"Orangina", "Tracy Beaker", "Erling Haaland", "Shrimp Jesus"} <= names
    # the ones explicitly cut
    assert not any(n in names for n in ("Bonnie Blue", "Clavicular",
                                        "David Goggins", "Ronnie Coleman",
                                        "Joe Wicks"))


def test_every_character_has_a_joke_engine_and_a_visual_lock():
    """A character without a trait is a costume and dies at episode two."""
    for char in load_roster().values():
        assert char.name and char.what and char.trait and char.visual
        assert char.cls in ("meme_character", "public_figure", "fictional_ip")


def test_the_real_people_are_marked_protected():
    roster = load_roster()
    assert roster["haaland"].protected and roster["clarkson"].protected
    assert roster["tracy_beaker"].protected      # fictional_ip counts too
    assert not roster["orangina"].protected      # a meme has no rights holder
    assert set(PROTECTED) == {"public_figure", "fictional_ip"}


def test_a_protected_characters_visual_lock_forces_caricature():
    """Rule 1 lives in the prompt the image model actually receives."""
    lock = get_character("haaland").visual_lock()
    assert "caricature" in lock.lower() and "never photorealistic" in lock.lower()
    assert "never photoreal" not in get_character("orangina").visual_lock().lower()


def test_the_fictional_character_is_drawn_in_our_style():
    assert "our own design" in get_character("tracy_beaker").visual.lower()


def test_the_cast_brief_flags_who_the_rules_apply_to():
    block = cast_block(resolve(["haaland", "orangina"]))
    assert "CARICATURE ONLY" in block
    assert block.count("CARICATURE ONLY") == 1     # only the protected one


def test_resolve_drops_the_unknown_and_dedupes():
    assert [c.key for c in resolve(["haaland", "nope", "haaland"])] == ["haaland"]
    assert resolve(None) == []


def test_the_world_caps_the_cast():
    assert load_world().max_cast == 4
    assert "Iron Palace" in load_world().as_block()


# --- the canon --------------------------------------------------------------


def test_an_empty_canon_says_it_is_episode_one(store):
    canon = load_canon(store)
    assert canon.next_number() == 1
    assert "FIRST episode" in canon.brief_block()


def test_recording_episodes_builds_the_recap(store):
    record_episode(store, change="Haaland took the last rack", idea_id="G1",
                   unresolved="who gets it tomorrow", cast=["haaland"])
    record_episode(store, change="Clarkson unplugged the treadmills", idea_id="G2")
    canon = load_canon(store)
    assert canon.next_number() == 3
    block = canon.brief_block()
    assert "ALREADY HAPPENED" in block
    assert "took the last rack" in block and "unplugged the treadmills" in block
    assert "COLD OPEN RULE" in block


def test_the_open_thread_is_the_debt_the_next_episode_owes(store):
    record_episode(store, change="a", unresolved="who gets the rack", idea_id="G1")
    record_episode(store, change="b", unresolved="whether Tracy is barred", idea_id="G2")
    canon = load_canon(store)
    assert canon.open_thread() == "whether Tracy is barred"
    assert "whether Tracy is barred" in canon.brief_block()


def test_rebuilding_the_same_post_is_not_a_new_episode(store):
    """A duplicated event silently corrupts every recap that follows."""
    record_episode(store, change="first go", idea_id="G1")
    record_episode(store, change="rewritten", idea_id="G1")
    canon = load_canon(store)
    assert len(canon.episodes) == 1
    assert canon.episodes[0].change == "rewritten"


def test_the_operator_can_author_the_canon_directly(store):
    """D16 — this is not an engine-only log."""
    canon = Canon()
    canon.season.label = "The Tournament"
    canon.episodes = [Episode(number=1, change="the bracket was drawn")]
    canon.standing = {"haaland": "undefeated and unbearable about it"}
    canon.notes = "Nobody mentions the hairdryer."
    save_canon(store, canon)

    block = load_canon(store).brief_block(cast_keys=["haaland"])
    assert "The Tournament" in block
    assert "undefeated and unbearable" in block
    assert "Nobody mentions the hairdryer" in block
    assert "OPERATOR'S NOTES" in block


def test_standing_is_scoped_to_this_episodes_cast(store):
    canon = Canon(standing={"haaland": "on top", "clarkson": "banned"})
    save_canon(store, canon)
    block = load_canon(store).brief_block(cast_keys=["haaland"])
    assert "on top" in block and "banned" not in block


def test_the_hard_reset_wipes_everything(store):
    """D16 — deliberately total: a half-reset leaves contradictions that are
    worse than starting over."""
    canon = Canon(episodes=[Episode(number=1, change="x")],
                  standing={"haaland": "y"}, notes="z")
    canon.season.label = "The Villa"
    save_canon(store, canon)

    fresh = reset_canon(store)
    assert fresh.episodes == [] and fresh.standing == {} and fresh.notes == ""
    assert load_canon(store).next_number() == 1


def test_a_corrupt_canon_does_not_brick_the_show(store):
    store.set_setting("multiverse_canon", "{not json at all")
    assert load_canon(store).next_number() == 1


def test_the_episode_brief_reaches_the_build(store):
    record_episode(store, change="Haaland took the last rack", idea_id="G0",
                   unresolved="who gets it tomorrow")
    msg = build_user_message(idea(show="multiverse", cast=["haaland", "clarkson"]),
                             store=store)
    assert "Iron Palace" in msg                 # the world
    assert "Erling Haaland" in msg              # the cast
    assert "took the last rack" in msg          # the canon
    assert "who gets it tomorrow" in msg        # the open thread
    assert "EPISODE OF **THE MULTIVERSE**" in msg   # and the show's own brief


def test_the_build_works_without_a_store():
    """The world and cast still land; only the canon needs the DB."""
    msg = build_user_message(idea(show="multiverse", cast=["orangina"]))
    assert "Iron Palace" in msg and "Orangina" in msg


def test_off_format_posts_get_none_of_this():
    msg = build_user_message(idea(show="amp"))
    assert "Iron Palace" not in msg and "THE CANON" not in msg


# --- the likeness gate ------------------------------------------------------


def test_only_the_multiverse_is_likeness_gated():
    assert is_blocking(idea(show="multiverse"))
    assert not is_blocking(idea(show="amp"))
    assert not is_blocking(idea())


def test_rule_2_no_real_person_next_to_a_product():
    """The sharpest legal edge in the show, and the easiest to simply avoid."""
    flags = lint_post(episode_post("Erling Haaland downs a CHRGD shaker"))
    assert "endorsement" in {f.rule for f in flags}
    # a meme character can hold whatever it likes
    assert not lint_post(episode_post("Orangina downs a CHRGD shaker"))


def test_rule_3_no_fabricated_statements():
    quoted = lint_post(episode_post('Jeremy Clarkson said "the gym is a scam"'))
    assert "fabricated_statement" in {f.rule for f in quoted}
    announced = lint_post(episode_post("Gordon Ramsay has admitted defeat"))
    assert "fabricated_statement" in {f.rule for f in announced}


def test_rule_1_no_photoreal_artwork_for_a_real_person():
    flags = lint_post(episode_post(
        "Erling Haaland waits for the rack",
        art="photorealistic 8k portrait, shot on a DSLR",
    ))
    assert "photoreal" in {f.rule for f in flags}
    # the same brief is fine when only meme characters are in it
    assert not lint_post(episode_post("Orangina waits", art="photorealistic 8k"))


def test_rule_4_off_limits_territory():
    for line in ("Tracy Beaker got arrested again",
                 "Jack Grealish looks fat in this light"):
        flags = lint_post(episode_post(line))
        assert "off_limits" in {f.rule for f in flags}, line


def test_rule_5_casting_is_closed():
    flags = lint_post(episode_post("someone does something"),
                      cast_keys=["haaland", "andrew_tate"])
    assert "off_roster" in {f.rule for f in flags}
    assert any("andrew_tate" in f.text for f in flags)


def test_rule_5_the_cast_cap_is_enforced_on_the_copy_too():
    """Declaring four and writing six is still six on the slide."""
    names = ["Erling Haaland", "Jack Grealish", "Gordon Ramsay",
             "Jeremy Clarkson", "Orangina", "Shrimp Jesus"]
    flags = lint_post(episode_post(*names))
    assert "too_many_characters" in {f.rule for f in flags}


def test_the_show_that_the_gate_must_not_break():
    """The jokes the show is actually made of have to pass, or the gate gets
    switched off and then it protects nothing."""
    clean = episode_post(
        "Haaland has been on the leg press for forty minutes",
        "Clarkson refuses to acknowledge the queue exists",
        "Orangina: but is it clean though",
        caption="who's getting the rack?",
    )
    assert lint_post(clean, cast_keys=["haaland", "clarkson", "orangina"]) == []


def test_named_characters_reads_the_copy_and_the_artwork():
    post = episode_post("The shark is back", art="Tralalero Tralala on a treadmill")
    assert "tralalero" in named_characters(post)


def test_a_judge_failure_never_breaks_the_build(settings, store):
    class Exploding:
        def judge(self, system, user):
            raise RuntimeError("api down")

    result = check_likeness(episode_post("a clean line"), idea(show="multiverse"),
                            settings, store=store, judge=Exploding())
    assert result.error and result.checked and result.safe


def test_the_judge_sees_the_canon(settings, store):
    record_episode(store, change="Haaland took the last rack", idea_id="G0")
    seen = {}

    class Fake:
        def judge(self, system, user):
            seen["user"] = user
            return json.dumps({"safe": True, "flags": []})

    check_likeness(episode_post("x"), idea(show="multiverse", cast=["haaland"]),
                   settings, store=store, judge=Fake())
    assert "took the last rack" in seen["user"]
    assert "Erling Haaland" in seen["user"]


def test_the_judge_only_runs_where_it_is_wanted(settings, store):
    seen = {"n": 0}

    class Counting:
        def judge(self, system, user):
            seen["n"] += 1
            return json.dumps({"safe": True, "flags": []})

    check_likeness(episode_post("x"), idea(show="amp"), settings, judge=Counting())
    assert seen["n"] == 0
    check_likeness(episode_post("x"), idea(show="multiverse"), settings,
                   store=store, judge=Counting())
    assert seen["n"] == 1


def test_a_flagged_episode_is_held_for_review(store, settings):
    """The end of the line: a likeness break reaches the build and the episode
    does not render."""
    from chrgd import pipeline

    store.add_idea(idea(show="multiverse", cast=["haaland"]))
    built = {
        "post_type": "carousel", "hook": "h", "hook_options": ["a", "b", "c"],
        "slides": [{"headline": "Erling Haaland swears by our CHRGD creatine",
                    "supporting": "s", "image_prompt": "p", "visual_intent": "v",
                    "role": "hook", "swipe_trigger": "t"}],
        "caption": "c", "comment_trigger": "who wins?", "hashtags": ["#gym"] * 6,
        "route": {"mechanic": "m", "visual_engine": "v", "primary_goal": "g",
                  "qa": {k: 10 for k in (
                      "hook", "swipe_loop", "identity_recognition",
                      "group_chat_share", "comment_fight", "saveability",
                      "visual_originality", "dopamine_density", "clarity",
                      "layout_safety", "claim_safety", "overall")}},
    }

    class FakeClient:
        def complete(self, system, user):
            return pipeline.LLMResult(content=json.dumps(built),
                                      prompt_tokens=1, completion_tokens=1)

    result = pipeline.build_single_idea(store, settings, "G1", client=FakeClient(),
                                        record_run=False)
    assert result.status is Status.review
    assert any("endorse" in r.lower() for r in result.qa_failures)
    saved = json.loads(store.get_idea("G1").route_json)
    assert saved["likeness"]["safe"] is False


# --- the locked designs reach the renderer ----------------------------------


def test_the_cast_lock_leads_the_image_prompt():
    from chrgd.brand import load_brand
    from chrgd.images import _cast_lock_for, compose_design_prompt
    from chrgd.models import Slide

    lock = _cast_lock_for(idea(show="multiverse", cast=["haaland", "orangina"]))
    assert "Erling Haaland" in lock and "Orangina" in lock
    assert "never photorealistic" in lock.lower()

    prompt = compose_design_prompt(
        Slide(headline="H", image_prompt="a gym"), load_brand(), None,
        cast_lock=lock,
    )
    assert prompt.index("Erling Haaland") < prompt.index("a gym")


def test_no_cast_means_no_lock():
    from chrgd.images import _cast_lock_for

    assert _cast_lock_for(idea(show="amp")) == ""
    assert _cast_lock_for(idea()) == ""
