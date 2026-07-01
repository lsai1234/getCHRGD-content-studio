"""Tests for Phase B2 / Milestone 7: the run-chain orchestrator."""

from __future__ import annotations

import json

import pytest

from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.orchestrate import run_chain
from chrgd.pipeline import LLMResult

# Reuse the pipeline test's post shape via a local helper.
from tests.test_pipeline import make_post_dict
from tests.test_trends import FakeSearch, _payload


class FakeChat:
    def __init__(self, contents):
        self._c = contents
        self.calls = 0

    def complete(self, system, user):
        c = self._c[min(self.calls, len(self._c) - 1)]
        self.calls += 1
        return LLMResult(content=c, prompt_tokens=1000, completion_tokens=500)


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_MAX_SPEND_PER_RUN=100,
    )


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


def test_chain_build_render_export(store, settings):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    store.add_idea(Idea(idea_id="G-0002", concept_note="b"))
    chat = FakeChat([json.dumps(make_post_dict())])  # every build passes QA

    summary = run_chain(
        store, settings, 2, dry_run=True, build_client=chat
    )

    assert set(summary.built) == {"G-0001", "G-0002"}
    assert set(summary.rendered) == {"G-0001", "G-0002"}
    assert set(summary.exported) == {"G-0001", "G-0002"}
    # rendered assets exist and posts are marked exported
    assert store.get_idea("G-0001").exported_at is not None
    assert store.get_idea("G-0001").status is Status.done


def test_chain_records_single_run_no_double_count(store, settings):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    run_chain(store, settings, 1, dry_run=True, build_client=FakeChat([json.dumps(make_post_dict())]))
    runs = store.list_runs()
    # exactly one 'run' row (build did NOT record its own), no 'render' row (dry-run).
    assert [r["command"] for r in runs] == ["run"]


def test_chain_scout_seeds_then_builds(store, settings):
    # No queued ideas initially; scout seeds them, then the chain builds them.
    summary = run_chain(
        store, settings, 2,
        scout=True, dry_run=True,
        trend_client=FakeSearch(_payload(2)),
        build_client=FakeChat([json.dumps(make_post_dict())]),
    )
    assert summary.scouted == 2
    assert len(summary.built) == 2


def test_chain_flags_review_not_exported(store, settings):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    bad = json.dumps(make_post_dict(hook=3))  # fails QA both attempts
    summary = run_chain(store, settings, 1, dry_run=True, build_client=FakeChat([bad, bad]))
    assert summary.built == []
    assert summary.review == ["G-0001"]
    assert summary.exported == []  # nothing rendered/exported


def test_chain_no_export_flag(store, settings):
    store.add_idea(Idea(idea_id="G-0001", concept_note="a"))
    summary = run_chain(
        store, settings, 1, dry_run=True, do_export=False,
        build_client=FakeChat([json.dumps(make_post_dict())]),
    )
    assert summary.rendered == ["G-0001"]
    assert summary.exported == []
