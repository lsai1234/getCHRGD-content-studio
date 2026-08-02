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
    Season,
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


def episode_post(*copy, art=None, caption="what happens next?"):
    """A finished episode.

    By default each slide's panel draws the beat its own copy describes — which
    is what a correct episode looks like, and what the drift check exists to
    require. Pass `art` explicitly when the test is about the artwork itself.
    """
    return {
        "slides": [{"headline": c, "supporting": "", "body": "",
                    "image_prompt": art or f"a comic panel in a gym: {c}",
                    "visual_intent": ""} for c in copy],
        "caption": caption,
        "comment_trigger": "who wins?",
    }


# --- the roster -------------------------------------------------------------


def test_the_roster_is_the_cast_that_was_agreed():
    roster = load_roster()
    assert len(roster) == 14
    protected = [c for c in roster.values() if c.protected]
    assert len(protected) == 6, "six guest stars, eight meme regulars"
    # Orangina's husband exists because her whole gag needs somebody to walk in
    assert "orangino" in roster and not roster["orangino"].protected
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
    canon.season.label = "The Tournament"
    save_canon(store, canon)

    fresh = reset_canon(store)
    assert fresh.episodes == [] and fresh.standing == {} and fresh.notes == ""
    assert not fresh.season.is_set()
    assert load_canon(store).next_number() == 1


# --- the show is a world, not a reality format ------------------------------


def test_a_fresh_canon_imposes_no_format_on_the_episode(store):
    """`THE SEASON: The Villa. A reality dating format…` used to be the first
    line of every brief, so every premise started inside a dating show."""
    block = load_canon(store).brief_block()
    assert "THE SEASON" not in block
    for word in ("villa", "dating", "coupling", "recoupling", "public vote"):
        assert word not in block.lower(), word
    assert "THIS IS EPISODE 1." in block


def test_the_retired_default_season_is_dropped_from_a_stored_canon(store):
    """A studio that has been running for weeks has the old default sitting in
    its canon. Nobody chose it, so it does not survive a read."""
    store.set_setting("multiverse_canon", json.dumps({
        "season": {"key": "villa", "label": "The Villa",
                   "premise": "A reality dating format at Iron Palace."},
        "episodes": [{"number": 1, "change": "x"}],
    }))
    canon = load_canon(store)
    assert not canon.season.is_set()
    assert "villa" not in canon.brief_block().lower()
    assert canon.next_number() == 2, "the history itself is untouched"


def test_a_season_the_operator_actually_chose_survives(store):
    save_canon(store, Canon(season=Season(key="tournament",
                                          label="The Tournament",
                                          premise="One rack. A bracket.")))
    block = load_canon(store).brief_block()
    assert "THE SEASON: The Tournament. One rack. A bracket." in block


def test_the_format_is_banned_everywhere_the_writer_reads():
    from chrgd.roster import load_process
    from chrgd.shows import get_show
    from chrgd.story import STORY_SYSTEM

    show = get_show("multiverse")
    assert "IT IS NOT A REALITY FORMAT" in show.voice.block
    assert any("recoupling" in b for b in show.voice.banned)
    assert "THIS IS NOT A REALITY FORMAT" in STORY_SYSTEM
    assert "not a reality format" in load_process().lower()


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
    assert "EPISODE OF **CHRGD MULTIVERSE**" in msg  # and the show's own brief


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
    assert not lint_post(episode_post("Orangina waits",
                                      art="photorealistic 8k of Orangina waiting"))


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
        "Orangina: it's not what it looks like",
        caption="who's getting the rack?",
    )
    assert lint_post(clean, cast_keys=["haaland", "clarkson", "orangina"]) == []


def test_named_characters_reads_the_copy_and_the_artwork():
    post = episode_post("The shark is back", art="Tralalero Tralala on a treadmill")
    assert "tralalero" in named_characters(post)


# --- drift: the panel must draw the beat the copy just described ------------


def test_a_panel_that_draws_a_different_character_is_caught():
    """The real failure: an episode about Jeremy Clarkson needing to earn his
    way INTO the sauna rendered him as the bouncer of the sauna queue."""
    post = episode_post("Jeremy Clarkson is told to do ten squats to get in",
                        art="Gordon Ramsay running the sauna queue")
    flags = lint_post(post)
    assert "panel_drift" in {f.rule for f in flags}
    assert any("Jeremy Clarkson" in f.why and "Gordon Ramsay" in f.why
               for f in flags)


def test_a_panel_with_nobody_in_it_is_caught():
    post = episode_post("Tracy Beaker denies everything",
                        art="a wide shot of the empty gym floor")
    assert "empty_panel" in {f.rule for f in lint_post(post)}


def test_a_panel_that_draws_the_right_person_passes():
    post = episode_post("Tracy Beaker denies everything",
                        art="Tracy Beaker with grease on both hands, shrugging")
    assert lint_post(post) == []


def test_a_surname_counts_as_naming_someone():
    """The engine writes 'Haaland has been on the leg press', not the full name
    every time — matching only full names scored those panels as empty."""
    from chrgd.roster import name_index

    index = name_index()
    assert index["haaland"].key == "haaland"
    assert index["clarkson"].key == "clarkson"
    # short or ambiguous tokens are not aliased — "Jack" would collide
    assert "jack" not in index
    post = episode_post("Haaland refuses to move", art="Haaland on the leg press")
    assert lint_post(post) == [] and named_characters(post) == ["haaland"]


def test_a_judge_failure_never_breaks_the_build(settings, store):
    class Exploding:
        def judge(self, system, user):
            raise RuntimeError("api down")

    result = check_likeness(episode_post("Orangina waits her turn"),
                            idea(show="multiverse"),
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


# --- the canon writes itself (and the gags accumulate) ----------------------


def _built_episode(*headlines):
    return {
        "post_type": "carousel", "hook": headlines[0],
        "hook_options": ["a", "b", "c"],
        "slides": [{"headline": h, "supporting": "s", "image_prompt": "a comic panel",
                    "visual_intent": "v", "role": "hook", "swipe_trigger": "t"}
                   for h in headlines],
        "caption": "who wins?", "comment_trigger": "who wins?",
        "hashtags": ["#gym"] * 6,
        "route": {"mechanic": "m", "visual_engine": "v", "primary_goal": "g",
                  "qa": {k: 10 for k in (
                      "hook", "swipe_loop", "identity_recognition",
                      "group_chat_share", "comment_fight", "saveability",
                      "visual_originality", "dopamine_density", "clarity",
                      "layout_safety", "claim_safety", "overall")}},
    }


class _Continuity:
    """A stand-in continuity editor: returns whatever record it was given."""

    def __init__(self, record):
        self._record = record

    def judge(self, system, user):
        self.saw = user
        return json.dumps(self._record)


def test_the_roster_seeds_every_character_with_real_jokes():
    """A trait sentence alone makes the engine invent a personality each week."""
    for char in load_roster().values():
        assert char.catchphrase, f"{char.key} has no catchphrase"
        assert len(char.bits) >= 3, f"{char.key} has too few signature bits"
    line = get_character("orangina").brief_line()
    assert "It's not what it looks like." in line
    assert "Established running gags" in line


def test_landed_gags_are_added_to_the_seed_ones_in_the_brief():
    from chrgd.roster import cast_block

    block = cast_block(resolve(["orangina"]),
                       gags={"orangina": ["appeals every ban on ingredient grounds"]})
    assert "appeals every ban" in block          # the landed one
    assert "not what it looks like" in block.lower()   # and the seed one
    assert "ESCALATE" in block


def test_a_build_records_its_own_episode(store, settings, monkeypatch):
    """The gap this closes: without it the canon only grows by hand and
    episode 2 opens knowing nothing about episode 1."""
    from chrgd import pipeline

    store.add_idea(idea(show="multiverse", cast=["orangina", "ramsay"]))
    built = _built_episode("Orangina inspects the sauna",
                           "Ramsay rates her three out of ten")

    class FakeClient:
        def complete(self, system, user):
            return pipeline.LLMResult(content=json.dumps(built),
                                      prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(
        pipeline, "build_user_message", pipeline.build_user_message
    )
    # no API key in the test settings → the continuity editor can't be built,
    # so this exercises the fallback path: the episode is still recorded.
    result = pipeline.build_single_idea(store, settings, "G1",
                                        client=FakeClient(), record_run=False)
    canon = load_canon(store)
    assert len(canon.episodes) == 1
    assert canon.episodes[0].idea_id == "G1"
    assert canon.episodes[0].cast == ["orangina", "ramsay"]
    assert canon.next_number() == 2
    assert result.status in (Status.done, Status.review)


def test_a_rebuild_updates_the_entry_rather_than_adding_another(store, settings):
    from chrgd import pipeline

    store.add_idea(idea(show="multiverse", cast=["orangina"]))
    built = _built_episode("Orangina inspects the sauna")

    class FakeClient:
        def complete(self, system, user):
            return pipeline.LLMResult(content=json.dumps(built),
                                      prompt_tokens=1, completion_tokens=1)

    pipeline.build_single_idea(store, settings, "G1", client=FakeClient(),
                               record_run=False)
    pipeline.build_single_idea(store, settings, "G1", client=FakeClient(),
                               record_run=False)
    assert len(load_canon(store).episodes) == 1


def test_a_non_multiverse_build_records_nothing(store, settings):
    from chrgd import pipeline

    store.add_idea(idea(show="amp"))
    built = _built_episode("Amp forgets his trainers")

    class FakeClient:
        def complete(self, system, user):
            return pipeline.LLMResult(content=json.dumps(built),
                                      prompt_tokens=1, completion_tokens=1)

    pipeline.build_single_idea(store, settings, "G1", client=FakeClient(),
                               record_run=False)
    assert load_canon(store).episodes == []


def test_the_continuity_editor_extracts_gags_and_standings(store):
    from chrgd.series import record_from_post

    judge = _Continuity({
        "title": "Clean Living",
        "change": "Orangina got barred from the sauna",
        "unresolved": "whether she appeals",
        "characters": {
            "orangina": {"standing": "barred from the sauna",
                         "gags": ["appeals every ban on ingredient grounds"],
                         "relationships": {"ramsay": "open warfare"}},
            "ramsay": {"standing": "undefeated at the protein bar",
                       "gags": ["rates people out of ten unprompted"]},
        },
    })
    record_from_post(store, {"slides": [{"headline": "x"}], "caption": "c"},
                     cast_keys=["orangina", "ramsay"], idea_id="G1", judge=judge)

    canon = load_canon(store)
    assert canon.episodes[0].title == "Clean Living"
    assert canon.open_thread() == "whether she appeals"
    assert canon.standing_for("orangina") == "barred from the sauna"
    assert canon.gags()["ramsay"] == ["rates people out of ten unprompted"]
    assert canon.characters["orangina"].relationships == {"ramsay": "open warfare"}
    assert canon.characters["orangina"].episodes == 1


def test_gags_accumulate_across_episodes(store):
    from chrgd.series import record_from_post

    for n, gag in enumerate(["appeals every ban", "brings a lawyer"], 1):
        record_from_post(
            store, {"slides": [{"headline": "x"}]}, cast_keys=["orangina"],
            idea_id=f"G{n}",
            judge=_Continuity({"change": f"c{n}", "unresolved": "u",
                               "characters": {"orangina": {"gags": [gag]}}}),
        )
    canon = load_canon(store)
    assert canon.gags()["orangina"] == ["appeals every ban", "brings a lawyer"]
    assert canon.characters["orangina"].episodes == 2


def test_a_repeated_gag_is_not_duplicated(store):
    from chrgd.series import record_from_post

    for n in (1, 2):
        record_from_post(
            store, {"slides": [{"headline": "x"}]}, cast_keys=["orangina"],
            idea_id=f"G{n}",
            judge=_Continuity({"change": "c", "characters": {
                "orangina": {"gags": ["appeals every ban"]}}}),
        )
    assert load_canon(store).gags()["orangina"] == ["appeals every ban"]


def test_gags_are_capped_so_the_brief_stays_lean(store):
    from chrgd.series import record_from_post

    for n in range(10):
        record_from_post(
            store, {"slides": [{"headline": "x"}]}, cast_keys=["orangina"],
            idea_id=f"G{n}",
            judge=_Continuity({"change": "c", "characters": {
                "orangina": {"gags": [f"gag {n}"]}}}),
        )
    gags = load_canon(store).gags()["orangina"]
    assert len(gags) == 6
    assert gags[-1] == "gag 9"       # the newest survive, the wrung-dry go


def test_the_continuity_editor_cannot_invent_a_character(store):
    """The roster is the allow-list here too."""
    from chrgd.series import record_from_post

    record_from_post(
        store, {"slides": [{"headline": "x"}]}, cast_keys=["orangina"],
        idea_id="G1",
        judge=_Continuity({"change": "c", "characters": {
            "orangina": {"gags": ["a real one"]},
            "andrew_tate": {"gags": ["should never appear"]}}}),
    )
    canon = load_canon(store)
    assert "andrew_tate" not in canon.characters
    assert "orangina" in canon.characters


def test_a_broken_extraction_still_records_the_episode(store):
    """A gap in a serial's memory is worse than an imprecise line."""
    from chrgd.series import record_from_post

    class Exploding:
        def judge(self, system, user):
            raise RuntimeError("api down")

    record_from_post(store, {"slides": [{"headline": "Orangina vs the sauna"},
                                        {"headline": "to be continued"}]},
                     cast_keys=["orangina"], idea_id="G1", judge=Exploding())
    canon = load_canon(store)
    assert len(canon.episodes) == 1
    assert "Orangina vs the sauna" in canon.episodes[0].change
    assert canon.episodes[0].unresolved == "to be continued"


def test_the_accumulated_gags_reach_the_next_episodes_brief(store):
    """The whole point: what episode 1 landed is in episode 2's instructions."""
    from chrgd.series import record_from_post

    record_from_post(
        store, {"slides": [{"headline": "x"}]}, cast_keys=["orangina"],
        idea_id="G1",
        judge=_Continuity({"change": "Orangina was barred", "unresolved": "the appeal",
                           "characters": {"orangina": {
                               "standing": "barred from the sauna",
                               "gags": ["appeals every ban on ingredient grounds"]}}}),
    )
    msg = build_user_message(idea(show="multiverse", cast=["orangina"]), store=store)
    assert "appeals every ban on ingredient grounds" in msg   # the landed gag
    assert "It's not what it looks like." in msg              # the seed catchphrase
    assert "barred from the sauna" in msg                     # where she stands
    assert "the appeal" in msg                                # the open thread
    assert "THIS IS EPISODE 2" in msg


# --- story craft: what makes a slide worth swiping --------------------------


def test_the_spine_is_chapters_with_events_not_abstract_roles():
    """A spine of moods produces six slides about a theme. A spine of chapters
    produces a story."""
    from chrgd.shows import get_show

    show = get_show("multiverse")
    assert show.spine.roles == [
        "the_situation", "the_stakes", "the_evidence", "the_false_lead",
        "the_reveal", "the_why", "the_choice", "the_payoff",
    ]
    briefs = " ".join(show.spine.briefs)
    # the two structural demands the story depends on
    assert "POINT AT THE WRONG PERSON" in briefs      # the clue misdirects
    assert "recontextualises the evidence" in briefs  # and the reveal pays it off
    assert "FLAW" in briefs                           # the turn is character-driven


def test_the_brief_teaches_swipe_craft():
    from chrgd.shows import get_show

    voice = get_show("multiverse").voice.block
    for rule in ("EVERY SLIDE IS A CHAPTER", "WRITE IN SCENES, NOT SUMMARY",
                 "THE SWIPE RULE", "PLANT, THEN PAY OFF",
                 "ONE CHARACTER PER BEAT"):
        assert rule in voice, rule


def test_the_brief_demands_the_reader_can_tell_who_is_who():
    from chrgd.shows import get_show

    voice = get_show("multiverse").voice.block
    assert "FULL NAME the first time" in voice
    assert "Tracy Beaker" in voice and "never just" in voice
    assert "Attribute dialogue explicitly" in voice


def test_tracy_beaker_introduces_herself_properly():
    """The recognition IS the hook — 'Tracy' alone means nothing to a scroller."""
    tracy = get_character("tracy_beaker")
    assert tracy.catchphrase == "My name is Tracy Beaker."
    bits = " ".join(tracy.bits).lower()
    assert "full name" in bits
    assert "hollywood" in bits          # the mum lie, her most recognisable trait
    assert "Tracy Beaker" in tracy.brief_line()


def test_the_show_is_branded_as_a_chrgd_product():
    """It keeps its own comic world, but it is openly a CHRGD thing."""
    from chrgd.shows import get_show

    show = get_show("multiverse")
    assert show.label == "CHRGD MULTIVERSE"
    assert show.look.title_card == "CHRGD MULTIVERSE"
    assert "electric blue" in show.look.title_note
    # the masthead does the branding, so the frame stays out of the artwork
    assert show.look.furniture.wordmark is False


def test_the_worked_example_is_loaded_as_a_standard_not_a_plot():
    from chrgd.roster import load_example

    example = load_example()
    assert "THE STANDARD TO WRITE TO" in example
    assert "completely different story" in example   # never reuse the plot
    assert "BACK SOON" in example                    # the episode itself
    # it teaches plainness first, and shows the slide cut as a table of beats
    assert "Match how PLAIN it is" in example
    assert example.count("|") >= 8


def test_the_example_reaches_an_episode_brief(store):
    msg = build_user_message(idea(show="multiverse", cast=["tracy_beaker"]),
                             store=store)
    assert "THE STANDARD TO WRITE TO" in msg
    assert "My name is Tracy Beaker." in msg     # her catchphrase, in the cast block
    assert "EVERY SLIDE IS A CHAPTER" in msg


def test_a_non_multiverse_brief_carries_none_of_it():
    msg = build_user_message(idea(show="amp"))
    assert "THE STANDARD TO WRITE TO" not in msg
    assert "BACK SOON" not in msg


# --- the reset button -------------------------------------------------------


def test_a_reset_returns_the_world_to_episode_one(store):
    """The operator's escape hatch: forget the history, start clean."""
    from chrgd.series import record_from_post

    for n in (1, 2, 3):
        record_from_post(
            store, {"slides": [{"headline": "x"}]}, cast_keys=["tracy_beaker"],
            idea_id=f"G{n}",
            judge=_Continuity({"change": f"episode {n}", "unresolved": "u",
                               "characters": {"tracy_beaker": {
                                   "standing": "secretly competent",
                                   "gags": [f"gag {n}"]}}}),
        )
    assert load_canon(store).next_number() == 4

    reset_canon(store)
    canon = load_canon(store)
    assert canon.next_number() == 1
    assert canon.episodes == []
    assert canon.gags() == {}                       # the accumulated bits go
    assert canon.standing_for("tracy_beaker") == ""  # and the standings
    assert "FIRST episode" in canon.brief_block()


def test_a_reset_does_not_touch_the_roster(store):
    """Seed bits live in config, not the canon — a reset forgets what the show
    established, not who the characters are."""
    from chrgd.series import record_from_post

    record_from_post(store, {"slides": [{"headline": "x"}]},
                     cast_keys=["tracy_beaker"], idea_id="G1",
                     judge=_Continuity({"change": "c", "characters": {
                         "tracy_beaker": {"gags": ["a landed one"]}}}))
    reset_canon(store)

    tracy = get_character("tracy_beaker")
    assert tracy.catchphrase == "My name is Tracy Beaker."
    assert len(tracy.bits) >= 3
    assert "My name is Tracy Beaker." in build_user_message(
        idea(show="multiverse", cast=["tracy_beaker"]), store=store
    )


def test_the_reset_endpoint_demands_an_explicit_confirmation(settings, store):
    from fastapi.testclient import TestClient

    from chrgd.config import Settings
    from chrgd.webapp import create_app

    st = Settings(CHRGD_DB_PATH=settings.db_path,
                  CHRGD_OUTPUT_DIR=settings.output_dir,
                  CHRGD_WEB_USERNAME="a", CHRGD_WEB_PASSWORD="b",
                  CHRGD_SECRET_KEY="k" * 32)
    client = TestClient(create_app(st))
    client.post("/login", data={"username": "a", "password": "b"},
                follow_redirects=False)

    assert client.post("/api/canon/reset", data={"confirm": "yes"}).status_code == 400
    assert client.post("/api/canon/reset", data={}).status_code in (400, 422)
    assert client.post("/api/canon/reset", data={"confirm": "RESET"}).status_code == 200


def test_the_reset_button_is_on_the_multiverse_screen(settings, store):
    """It only appears once there's history — nothing to reset before that."""
    from fastapi.testclient import TestClient

    from chrgd.config import Settings
    from chrgd.series import record_from_post
    from chrgd.webapp import create_app

    st = Settings(CHRGD_DB_PATH=settings.db_path,
                  CHRGD_OUTPUT_DIR=settings.output_dir,
                  CHRGD_WEB_USERNAME="a", CHRGD_WEB_PASSWORD="b",
                  CHRGD_SECRET_KEY="k" * 32)
    client = TestClient(create_app(st))
    client.post("/login", data={"username": "a", "password": "b"},
                follow_redirects=False)

    assert 'id="canon-reset"' not in client.get("/create").text

    record_from_post(store, {"slides": [{"headline": "x"}]},
                     cast_keys=["tracy_beaker"], idea_id="G1",
                     judge=_Continuity({"change": "something happened"}))
    page = client.get("/create").text
    assert 'id="canon-reset"' in page
    assert 'data-episodes="1"' in page      # the confirmation names the cost
    assert "Episode 2" in page



def test_orangina_is_the_character_she_actually_is():
    """She was invented wrong first time round — a 'wellness girl' who talked
    about vitamin C. Her real bit is the affair, and the joke is the lying."""
    orangina = get_character("orangina")
    assert "faithful" in orangina.trait.lower()
    assert orangina.catchphrase == "It's not what it looks like."
    bits = " ".join(orangina.bits).lower()
    assert "excuse" in bits and "husband" in bits
    # and every trace of the invented version is gone
    blob = (orangina.trait + orangina.role + orangina.catchphrase
            + " ".join(orangina.bits)).lower()
    for invented in ("vitamin c", "wellness", "is it clean", "ingredients list"):
        assert invented not in blob, invented


def test_her_husband_can_actually_be_drawn():
    """He appears in panels, so he needs a locked visual like anyone else —
    otherwise he's a different orange every episode."""
    husband = get_character("orangino")
    assert husband.visual and "orange" in husband.visual.lower()
    assert husband.catchphrase and husband.bits
    assert "walk" in " ".join(husband.bits).lower() or "arrives" in " ".join(husband.bits).lower()


def test_her_register_is_farce_not_smut():
    """The show already bans anything sexual; this makes the boundary explicit
    for the one character whose premise sits nearest it."""
    from chrgd.shows import get_show

    voice = get_show("multiverse").voice.block
    assert "FARCE, never smut" in voice
    assert "the joke is the lying" in voice.lower()


def test_the_worked_example_no_longer_teaches_the_invented_character():
    """The example is what the engine imitates, so a wrong character in it
    propagates into every episode."""
    from chrgd.roster import load_example

    example = load_example().lower()
    for invented in ("vitamin c", "ingredients list", "is it clean"):
        assert invented not in example, invented
