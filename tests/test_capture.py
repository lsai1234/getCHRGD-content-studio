"""Tests for milestone 1: the SQLite store and idea capture."""

from __future__ import annotations

import pytest

from chrgd.capture import capture_ideas, split_dump
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import DecaySpeed, Idea, Status


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def settings(tmp_path):
    return Settings(CHRGD_DB_PATH=tmp_path / "test.db", CHRGD_ID_PREFIX="G")


# --- split_dump -------------------------------------------------------------


def test_split_on_newlines():
    assert split_dump("first idea\nsecond idea") == ["first idea", "second idea"]


def test_split_on_semicolons():
    assert split_dump("a; b; c") == ["a", "b", "c"]


def test_strips_bullets_and_numbers():
    dump = "- gym bros\n* pre-workout\n1. queue rage\n2) locker etiquette"
    assert split_dump(dump) == [
        "gym bros",
        "pre-workout",
        "queue rage",
        "locker etiquette",
    ]


def test_ignores_blank_lines():
    assert split_dump("one\n\n\ntwo\n") == ["one", "two"]


def test_single_line_is_one_idea():
    assert split_dump("just the one thought") == ["just the one thought"]


# --- id minting -------------------------------------------------------------


def test_id_sequence_starts_at_one(store):
    assert store.next_idea_id("G") == "G-0001"


def test_id_sequence_continues(store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    store.add_idea(Idea(idea_id="G-0002", concept_note="b"))
    assert store.next_idea_id("G") == "G-0003"


# --- add_idea + reads -------------------------------------------------------


def test_add_and_get_roundtrip(store):
    idea = Idea(idea_id="G-0001", concept_note="locker hoggers", priority=1)
    store.add_idea(idea)
    got = store.get_idea("G-0001")
    assert got is not None
    assert got.concept_note == "locker hoggers"
    assert got.priority == 1
    assert got.status is Status.queued


def test_add_idea_is_idempotent(store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    store.add_idea(Idea(idea_id="G-0001", concept_note="changed"))
    assert store.count() == 1
    # Original row is preserved, not overwritten.
    assert store.get_idea("G-0001").concept_note == "a"


def test_next_unprocessed_orders_by_priority(store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="low", priority=5))
    store.add_idea(Idea(idea_id="G-0002", concept_note="high", priority=1))
    ideas = store.next_unprocessed(10)
    assert [i.idea_id for i in ideas] == ["G-0002", "G-0001"]


def test_next_unprocessed_excludes_non_queued(store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    store.add_idea(Idea(idea_id="G-0002", concept_note="b"))
    store.mark_processing("G-0001")
    ideas = store.next_unprocessed(10)
    assert [i.idea_id for i in ideas] == ["G-0002"]


# --- build fields + export --------------------------------------------------


def test_save_build_marks_done(store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    store.save_build("G-0001", {"hook": "you're the locker hogger", "caption": "x"})
    got = store.get_idea("G-0001")
    assert got.status is Status.done
    assert got.hook == "you're the locker hogger"


def test_mark_exported_is_idempotent(store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    store.mark_exported("G-0001")
    first = store.get_idea("G-0001").exported_at
    store.mark_exported("G-0001")
    second = store.get_idea("G-0001").exported_at
    assert first is not None
    assert first == second  # second call did not overwrite


# --- capture_ideas end to end ----------------------------------------------


def test_capture_creates_rows(store, settings):
    created, skipped = capture_ideas(
        store, settings, "idea one\nidea two", priority=2, content_category="humour"
    )
    assert len(created) == 2
    assert skipped == []
    assert created[0].idea_id == "G-0001"
    assert created[0].priority == 2
    assert created[0].content_category == "humour"


def test_capture_dedupes(store, settings):
    capture_ideas(store, settings, "same idea")
    created, skipped = capture_ideas(store, settings, "same idea")
    assert created == []
    assert skipped == ["same idea"]
    assert store.count() == 1


def test_capture_carries_decay_speed(store, settings):
    created, _ = capture_ideas(
        store, settings, "heatwave gym", decay_speed=DecaySpeed.days
    )
    assert created[0].decay_speed is DecaySpeed.days
