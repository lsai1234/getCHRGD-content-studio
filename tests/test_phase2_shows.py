"""Phase 2 — STRAIGHT UP's claims gate and library, THE SESSION's variant matrix.

The claims gate is the one place in this codebase where a check is allowed to
STOP a post rather than just flag it, so most of what's tested here is the
boundary: what blocks, what's merely advisory, and what must never be flagged
at all (a blunt opinion about a price is the show's whole job).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from chrgd.claims import check_claims, is_blocking, lint_post, parse_verdict
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.ingredients import covered, get_ingredient, load_ingredients
from chrgd.models import Idea, Status
from chrgd.pipeline import build_user_message
from chrgd.sessions import (
    brief_block,
    describe,
    load_axes,
    missing_required,
    stale_options,
    suggestion,
    validate,
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


def post_with(*texts, caption="Save this.") -> dict:
    return {
        "slides": [{"headline": t, "supporting": "", "body": ""} for t in texts],
        "caption": caption,
        "comment_trigger": "which one are you?",
    }


# --- the claims lint --------------------------------------------------------


@pytest.mark.parametrize("text,rule", [
    ("Guaranteed results in 30 days", "guaranteed"),
    ("Clinically proven to work", "clinically_proven"),
    ("This cures your soreness", "cures"),
    ("Boosts your immune system", "immune_claim"),
    ("A proper detox for your liver", "detox"),
    ("It melts fat while you sleep", "melts_fat"),
    ("Boosts testosterone naturally", "testosterone_boost"),
    ("Studies show it works", "vague_studies"),
    ("You'll lose a stone by summer", "outcome_promise"),
    ("The #1 pre-workout", "number_one"),
])
def test_the_lint_catches_the_unambiguous_stuff(text, rule):
    flags = lint_post(post_with(text))
    assert rule in {f.rule for f in flags}, f"{text!r} should trip {rule}"


@pytest.mark.parametrize("text", [
    # the show's actual job: sharp about the industry, neutral about biology
    "A proprietary blend hides the dose. That's the point of it.",
    "£45 for flavoured maltodextrin is a choice they made, not a cost they bore",
    "The evidence here is thinner than the marketing suggests",
    "Around 3-5g a day. Loading is optional.",
    "Most people don't need this, and the label knows it",
    # ordinary training copy
    "Three sets of eight, stop with about two reps left in you",
    "Send this to the mate who still skips leg day",
])
def test_the_lint_does_not_flag_the_writing_the_show_exists_to_do(text):
    """Over-flagging is its own failure — it trains the editor to ignore the
    gate, which is worse than not having one."""
    assert lint_post(post_with(text)) == []


def test_flags_carry_where_they_came_from():
    flags = lint_post({
        "slides": [{"headline": "fine"}, {"headline": "guaranteed gains"}],
        "caption": "clinically proven",
        "comment_trigger": "which one are you?",
    })
    assert {f.where for f in flags} == {"slide 2", "caption"}
    assert all(f.why for f in flags)


def test_one_flag_per_rule_per_location():
    """Two flags for one phrase reads as two problems."""
    flags = lint_post(post_with("guaranteed, guaranteed, guaranteed"))
    assert len(flags) == 1


def test_the_lint_reads_the_body_block_too():
    """The body is the densest, most claim-prone text on a slide."""
    post = {"slides": [{"headline": "Creatine", "supporting": "",
                        "body": "It is clinically proven."}], "caption": ""}
    assert lint_post(post)


# --- what blocks, and what's only advisory ----------------------------------


def test_only_a_claims_gated_show_holds_a_post():
    assert is_blocking(idea(show="straight_up"))
    assert not is_blocking(idea(show="amp"))
    assert not is_blocking(idea(show="session"))
    assert not is_blocking(idea())          # off-format


def test_the_lint_still_runs_everywhere(settings):
    """A guaranteed-outcome promise is a problem whichever show wrote it — it
    just doesn't hold the post outside STRAIGHT UP."""
    result = check_claims(post_with("guaranteed gains"), idea(show="amp"),
                          settings, use_judge=False)
    assert result.checked and result.flags and not result.safe
    assert result.blocking is False


def test_a_clean_straight_up_post_passes(settings):
    result = check_claims(
        post_with("What a proprietary blend hides", caption="Save this."),
        idea(show="straight_up"), settings, use_judge=False,
    )
    assert result.safe and result.blocking and not result.flags


def test_a_judge_failure_never_breaks_the_build(settings):
    class Exploding:
        def judge(self, system, user):
            raise RuntimeError("api down")

    result = check_claims(post_with("fine copy"), idea(show="straight_up"),
                          settings, judge=Exploding())
    assert result.error and result.checked
    # the lint's verdict still stands — the gate degrades, it doesn't vanish
    assert result.safe


def test_the_judge_adds_what_a_regex_cannot_see(settings):
    class Fake:
        def judge(self, system, user):
            return json.dumps({
                "safe": False, "note": "implied promise",
                "flags": [{"where": "slide 1", "text": "drop two dress sizes",
                           "rule": "judge", "why": "promises an outcome"}],
            })

    result = check_claims(post_with("drop two dress sizes"),
                          idea(show="straight_up"), settings, judge=Fake())
    assert not result.safe
    assert any(f.rule == "judge" for f in result.flags)
    assert result.note == "implied promise"


def test_the_judge_is_deduped_against_the_lint(settings):
    class Echo:
        def judge(self, system, user):
            return json.dumps({"safe": False, "flags": [
                {"where": "slide 1", "text": "guaranteed gains",
                 "rule": "judge", "why": "promises an outcome"}]})

    result = check_claims(post_with("guaranteed gains"),
                          idea(show="straight_up"), settings, judge=Echo())
    assert len(result.flags) == 1


def test_the_judge_only_runs_where_it_is_wanted(settings):
    seen = {"n": 0}

    class Counting:
        def judge(self, system, user):
            seen["n"] += 1
            return json.dumps({"safe": True, "flags": []})

    check_claims(post_with("fine"), idea(show="amp"), settings, judge=Counting())
    assert seen["n"] == 0, "the judge costs money — don't spend it off-show"
    check_claims(post_with("fine"), idea(show="straight_up"), settings,
                 judge=Counting())
    assert seen["n"] == 1


def test_verdict_parsing_survives_a_fenced_response():
    verdict = parse_verdict('```json\n{"safe": true, "flags": []}\n```')
    assert verdict.safe and verdict.flags == []
    with pytest.raises(ValueError):
        parse_verdict("no json here")


def test_a_flagged_straight_up_build_is_held_for_review(store, settings, monkeypatch):
    """The end of the line: a claim reaches the build and the post does not
    ship, it lands in review with the reason attached."""
    from chrgd import pipeline

    store.add_idea(idea(show="straight_up"))
    built = {
        "post_type": "carousel", "hook": "h",
        "hook_options": ["a", "b", "c"],
        "slides": [{"headline": "Clinically proven to work", "supporting": "s",
                    "image_prompt": "p", "visual_intent": "v", "role": "hook",
                    "swipe_trigger": "t"}],
        "caption": "c", "comment_trigger": "which one are you?",
        "hashtags": ["#gym"] * 6,
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
    assert any("clinically proven" in r.lower() for r in result.qa_failures)
    saved = store.get_idea("G1")
    assert saved.status is Status.review
    assert json.loads(saved.route_json)["claims"]["safe"] is False


def test_a_clean_build_still_ships(store, settings):
    """The null case: the gate must not turn into a tax on every post."""
    from chrgd import pipeline

    store.add_idea(idea(show="straight_up"))
    built = {
        "post_type": "carousel", "hook": "h", "hook_options": ["a", "b", "c"],
        "slides": [{"headline": "What a proprietary blend hides",
                    "supporting": "The dose isn't on the label.",
                    "image_prompt": "p", "visual_intent": "v", "role": "hook",
                    "swipe_trigger": "t"}],
        "caption": "Save this before your next tub.",
        "comment_trigger": "which one are you?", "hashtags": ["#gym"] * 6,
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
    assert result.status is Status.done
    assert json.loads(store.get_idea("G1").route_json)["claims"]["safe"] is True


# --- the ingredient library -------------------------------------------------


def test_the_library_is_a_real_backlog():
    lib = load_ingredients()
    assert len(lib) >= 10
    for entry in lib.values():
        assert entry.label and entry.question and entry.evidence
        assert entry.myth and entry.industry


def test_every_entry_carries_the_empty_product_hook():
    """D7 — designed in, deliberately unpopulated. When there's a range,
    wiring it in is filling a field."""
    assert all(i.our_product == "" for i in load_ingredients().values())


def test_an_entry_with_no_product_says_so_in_the_brief():
    brief = get_ingredient("creatine").brief_block()
    assert "no product in this category" in brief.lower()
    assert "THE MYTH" in brief and "INDUSTRY" in brief


def test_a_populated_product_is_mentioned_once():
    entry = get_ingredient("creatine").model_copy(
        update={"our_product": "CHRGD Creatine, 5g a serving"}
    )
    brief = entry.brief_block()
    assert "mentioned once" in brief and "CHRGD Creatine" in brief
    assert "no product in this category" not in brief.lower()


def test_the_ingredient_reaches_the_build():
    msg = build_user_message(idea(show="straight_up", ingredient="caffeine"))
    assert "THE SUBJECT: Caffeine" in msg
    assert "expensive coffee" in msg


def test_covered_ingredients_are_tracked_not_excluded(store):
    store.add_idea(Idea(idea_id="G9", concept_note="x",
                        route_json=json.dumps({"ingredient": "creatine"})))
    assert covered(store) == {"creatine"}
    assert get_ingredient("creatine") is not None   # still selectable


# --- the session variant matrix ---------------------------------------------


FULL = {"goal": "lower_body", "when": "after_work", "how": "weights",
        "where": "full_gym"}


def test_the_matrix_is_goal_framed_with_no_gender_option():
    """D11 — there is deliberately no gender axis to pick, so a post cannot
    accidentally acquire one."""
    axes = load_axes()
    assert "goal" in axes and axes["goal"].required
    import re

    everything = " ".join(
        f"{a.label} " + " ".join(a.options.values()) for a in axes.values()
    ).lower()
    for word in ("girls", "lads", "women", "men", "female", "male", "blokes"):
        assert not re.search(rf"\b{word}\b", everything), f"{word!r} in the matrix"


def test_no_equipment_is_a_first_class_option():
    assert "no_equipment" in load_axes()["where"].options


def test_required_axes_are_enforced_at_the_edge():
    assert missing_required(FULL) == []
    missing = missing_required({"goal": "lower_body"})
    assert "When" in missing and "How" in missing and "Where" in missing


def test_validate_drops_the_unknown_rather_than_raising():
    """A renamed option shouldn't 500 an old bookmark."""
    assert validate({**FULL, "nonsense": "x", "goal": "not_an_option"}) == {
        "when": "after_work", "how": "weights", "where": "full_gym"
    }
    assert validate({}) == {}
    assert describe(FULL)[0].startswith("Goal: Lower body")


def test_the_variant_reaches_the_build_with_its_special_rules():
    msg = build_user_message(idea(
        show="session",
        session_variant={**FULL, "where": "no_equipment",
                         "level": "first_month", "supplement": "none"},
    ))
    assert "THIS SESSION'S VARIANT" in msg
    assert "nothing but a floor" in msg          # no-equipment
    assert "no training history" in msg          # first month
    assert "drop that slide" in msg              # no supplement tie-in


def test_an_empty_variant_adds_nothing():
    assert brief_block({}) == ""
    assert "THIS SESSION'S VARIANT" not in build_user_message(idea(show="session"))


# --- coverage nudging (D12) -------------------------------------------------


def _session_idea(store, idea_id: str, variant: dict, *, days_ago: int = 0):
    store.add_idea(Idea(idea_id=idea_id, concept_note="x",
                        route_json=json.dumps({"show": "session",
                                               "session_variant": variant})))
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    store.conn.execute("UPDATE ideas SET created_at = ? WHERE idea_id = ?",
                       (when.isoformat(), idea_id))
    store.conn.commit()


def test_everything_is_stale_before_anything_is_posted(store):
    stale = stale_options(store)
    assert {s["option"] for s in stale} >= {"lower_body", "no_equipment"}
    assert all(s["days_since"] is None for s in stale)


def test_a_recent_session_clears_its_options(store):
    _session_idea(store, "G1", FULL, days_ago=1)
    stale = {s["option"] for s in stale_options(store)}
    assert "lower_body" not in stale and "after_work" not in stale
    assert "no_equipment" in stale     # untouched axes stay stale


def test_an_old_session_goes_stale_again(store):
    _session_idea(store, "G1", FULL, days_ago=40)
    assert "lower_body" in {s["option"] for s in stale_options(store)}


def test_never_used_options_are_suggested_first(store):
    _session_idea(store, "G1", FULL, days_ago=30)
    stale = stale_options(store)
    assert stale[0]["days_since"] is None


def test_the_nudge_is_one_readable_line(store):
    assert "not done a" in suggestion(store)
    _session_idea(store, "G1", FULL, days_ago=1)
    assert suggestion(store)      # other options are still uncovered


def test_coverage_never_blocks_a_repeat(store):
    """D12 — two lower-body sessions in a week is the operator's call. Nothing
    in this module can prevent it; it only reports."""
    _session_idea(store, "G1", FULL, days_ago=0)
    assert missing_required(FULL) == []
    assert validate(FULL) == FULL
    assert brief_block(FULL)
