"""The speed work: what runs at the same time, and what must not.

These are behaviour tests, not benchmarks. Each one pins a property that the
parallel paths have to keep — the order slides come back in, the anchor every
slide is generated against, the spend cap, the chain a pan set depends on —
because those are the things a fan-out silently breaks.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from PIL import Image

from chrgd import images
from chrgd.config import Settings
from chrgd.models import Idea
from chrgd.parallel import run_all, workers_for


def make_idea(idea_id="G-0001", slides=5):
    payload = [
        {
            "headline": f"Slide {i} headline",
            "supporting": "A supporting line.",
            "image_prompt": f"a UK gym scene number {i}",
            "visual_intent": "intent",
        }
        for i in range(slides)
    ]
    return Idea(idea_id=idea_id, concept_note="x", slides_json=json.dumps(payload))


@pytest.fixture()
def settings(tmp_path):
    s = Settings(CHRGD_OUTPUT_DIR=tmp_path / "out", CHRGD_DB_PATH=tmp_path / "t.db")
    s.openai_api_key = "sk-test"
    return s


class Recorder:
    """Stands in for the image API: records overlap and what it was handed."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.calls: list[dict] = []

    def __call__(self, prompt, settings, brand, quality, references=None):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.calls.append(
                {"prompt": prompt, "quality": quality,
                 "refs": 0 if references is None else len(references)}
            )
        try:
            time.sleep(self.delay)
        finally:
            with self.lock:
                self.in_flight -= 1
        return Image.new("RGB", (100, 150))


# --- the render fan-out -------------------------------------------------------


def test_slides_after_the_first_generate_together(settings, monkeypatch):
    """The whole point: five slides, not five waits laid end to end."""
    rec = Recorder()
    monkeypatch.setattr(images, "_generate_background", rec)
    settings.render_concurrency = 4

    result = images.render_carousel(make_idea(), settings, dry_run=False)

    assert len(result.paths) == 5
    assert rec.peak > 1, "slides 2-5 still went one at a time"
    assert rec.peak <= 4, "more images in flight than the configured limit"


def test_slide_one_is_rendered_alone_and_first(settings, monkeypatch):
    """It is the anchor — nothing else may start before it exists."""
    order: list[str] = []
    lock = threading.Lock()

    def fake(prompt, s, brand, quality, references=None):
        with lock:
            order.append("anchor" if references is None else "rest")
        time.sleep(0.02)
        return Image.new("RGB", (100, 150))

    monkeypatch.setattr(images, "_generate_background", fake)
    images.render_carousel(make_idea(), settings, dry_run=False)

    assert order[0] == "anchor"
    assert order.count("anchor") == 1
    assert set(order[1:]) == {"rest"}


def test_every_later_slide_is_still_anchored_to_slide_one(settings, monkeypatch):
    """Continuity is the thing a fan-out is most likely to quietly drop."""
    rec = Recorder(delay=0.01)
    monkeypatch.setattr(images, "_generate_background", rec)

    images.render_carousel(make_idea(), settings, dry_run=False)

    first, rest = rec.calls[0], rec.calls[1:]
    assert first["refs"] == 0          # nothing to anchor to yet
    assert all(c["refs"] == 1 for c in rest)  # …and everyone else has the anchor


def test_parallel_render_keeps_the_slides_in_order(settings, monkeypatch):
    """Slide 3 must land in position 3 however the completion order fell."""
    # Finish in reverse: the last slide submitted returns first.
    def fake(prompt, s, brand, quality, references=None):
        n = int(prompt.split("number ")[1].split()[0]) if "number " in prompt else 0
        time.sleep(0.05 - n * 0.008)
        return Image.new("RGB", (100, 150))

    monkeypatch.setattr(images, "_generate_background", fake)
    result = images.render_carousel(make_idea(), settings, dry_run=False)

    assert [p.rsplit("/", 1)[-1] for p in result.paths] == [
        f"slide_{i}.jpg" for i in range(1, 6)
    ]


def test_progress_is_reported_once_per_slide(settings, monkeypatch):
    monkeypatch.setattr(images, "_generate_background", Recorder(delay=0.01))
    seen: list[tuple[int, int]] = []
    images.render_carousel(
        make_idea(), settings, dry_run=False, on_slide=lambda d, t: seen.append((d, t))
    )
    assert len(seen) == 5
    assert [d for d, _ in seen] == [1, 2, 3, 4, 5]  # monotonic, never repeated
    assert all(total == 5 for _, total in seen)


def test_a_pan_set_stays_sequential(settings, monkeypatch):
    """Each pan slide continues the previous one's edge — a real dependency."""
    rec = Recorder(delay=0.02)
    monkeypatch.setattr(images, "_generate_background", rec)
    settings.render_concurrency = 4

    images.render_carousel(make_idea(), settings, dry_run=False, swipe_style="pan")

    assert rec.peak == 1


def test_concurrency_one_renders_one_at_a_time(settings, monkeypatch):
    rec = Recorder(delay=0.02)
    monkeypatch.setattr(images, "_generate_background", rec)
    settings.render_concurrency = 1

    images.render_carousel(make_idea(), settings, dry_run=False)

    assert rec.peak == 1


def test_parallel_render_costs_exactly_what_serial_did(settings, monkeypatch):
    monkeypatch.setattr(images, "_generate_background", Recorder(delay=0.0))
    from chrgd.brand import load_brand

    gen = load_brand().generation
    result = images.render_carousel(make_idea(), settings, dry_run=False)
    expected = (
        gen.variants_first * images._IMAGE_COST[gen.quality_first]
        + 4 * images._IMAGE_COST[gen.quality_rest]
    )
    assert result.spend_usd == pytest.approx(expected)
    assert result.generated == 4 + gen.variants_first


def test_the_spend_cap_still_stops_a_parallel_render(settings, monkeypatch):
    """Concurrency must not become a way to spend past the guard."""
    monkeypatch.setattr(images, "_generate_background", Recorder(delay=0.0))
    # Enough for slide 1 and a slide or two, nowhere near all five.
    settings.max_spend_per_run = 0.2
    settings.render_concurrency = 4

    with pytest.raises(images.ImageError, match="spend cap"):
        images.render_carousel(make_idea(), settings, dry_run=False)


def test_a_single_slide_post_still_renders(settings, monkeypatch):
    monkeypatch.setattr(images, "_generate_background", Recorder(delay=0.0))
    result = images.render_carousel(make_idea(slides=1), settings, dry_run=False)
    assert len(result.paths) == 1 and result.paths[0].endswith("slide_1.jpg")


def test_dry_run_is_unchanged(settings):
    result = images.render_carousel(make_idea(), settings, dry_run=True)
    assert len(result.paths) == 5
    assert result.spend_usd == 0.0
    assert all(p.endswith(".jpg") for p in result.paths)


# --- the batch build ----------------------------------------------------------


def _seed(store, n):
    from chrgd.models import Idea as I

    for i in range(n):
        store.add_idea(I(idea_id=f"G-{i:04d}", concept_note=f"idea {i}"))


class FakeBuildClient:
    """A build client that records how many writes were in flight at once."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0

    def complete(self, system, user):
        from chrgd.pipeline import LLMResult

        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.delay)
        finally:
            with self.lock:
                self.in_flight -= 1
        return LLMResult(content=json.dumps(_good_post()), prompt_tokens=10,
                         completion_tokens=10)


def _good_post():
    return {
        "post_type": "carousel",
        "hook": "A hook that stops the thumb",
        "slides": [
            {"headline": "one", "supporting": "", "image_prompt": "p",
             "visual_intent": "v"},
            {"headline": "two", "supporting": "", "image_prompt": "p",
             "visual_intent": "v"},
        ],
        "caption": "caption line",
        "comment_trigger": "which one are you",
        "hashtags": ["#gym"],
        "route": {
            "mechanic": "m", "visual_engine": "v", "primary_goal": "g",
            "build_note": "n",
            "qa": {"hook": 9, "swipe_loop": 9, "identity_recognition": 9,
                   "group_chat_share": 9, "comment_fight": 9, "saveability": 9,
                   "visual_originality": 9, "dopamine_density": 9, "clarity": 9,
                   "layout_safety": 9, "claim_safety": 9, "overall": 9},
        },
    }


@pytest.fixture()
def store(tmp_path):
    from chrgd.db import Store

    return Store(tmp_path / "t.db")


def test_a_batch_of_builds_goes_out_in_waves(settings, store):
    from chrgd.pipeline import build_ideas

    _seed(store, 6)
    settings.build_concurrency = 3
    client = FakeBuildClient()

    results = build_ideas(store, settings, 6, client=client)

    assert len(results) == 6
    assert all(r.status.value == "done" for r in results)
    assert client.peak > 1, "the batch still wrote one post at a time"
    assert client.peak <= 3


def test_batch_builds_are_persisted_in_queue_order(settings, store):
    from chrgd.pipeline import build_ideas

    _seed(store, 4)
    settings.build_concurrency = 4
    results = build_ideas(store, settings, 4, client=FakeBuildClient(delay=0.02))

    assert [r.idea_id for r in results] == [f"G-{i:04d}" for i in range(4)]
    for i in range(4):
        assert store.get_idea(f"G-{i:04d}").slides_json


def test_build_concurrency_one_is_the_old_serial_batch(settings, store):
    from chrgd.pipeline import build_ideas

    _seed(store, 3)
    settings.build_concurrency = 1
    client = FakeBuildClient(delay=0.02)
    build_ideas(store, settings, 3, client=client)
    assert client.peak == 1


def test_a_hard_llm_error_still_stops_the_batch(settings, store):
    """An auth/network failure hits every idea — don't burn the whole queue."""
    from chrgd.pipeline import LLMError, build_ideas

    _seed(store, 6)
    settings.build_concurrency = 2

    class Dead:
        def complete(self, system, user):
            raise LLMError("no key")

    results = build_ideas(store, settings, 6, client=Dead())

    assert all(r.error for r in results)
    assert len(results) < 6                      # it stopped, it didn't grind on
    # Nothing is stranded mid-flight: every idea is queued again for a retry.
    assert store.count() == 6
    assert all(i.status.value == "queued" for i in store.list_ideas())


def test_the_spend_cap_ends_a_batch_between_waves(settings, store):
    """The guard survives the fan-out — it is just checked per wave now."""
    from chrgd.pipeline import build_ideas, estimate_cost

    _seed(store, 6)
    settings.build_concurrency = 2
    per_post = estimate_cost(settings.openai_model, 10, 10)
    # Enough for the first wave of two, not for a second.
    settings.max_spend_per_run = per_post * 1.5

    results = build_ideas(store, settings, 6, client=FakeBuildClient(delay=0.0))

    built = [r for r in results if r.status.value == "done"]
    assert len(built) == 2                              # the first wave landed
    assert any("spend cap" in (r.error or "") for r in results)
    assert len(results) == 3                            # …and then it stopped


# --- the fast lane ------------------------------------------------------------


def test_two_fast_workers_run_two_jobs_at_once(settings, tmp_path, store):
    """A render must not sit behind a build for the whole of its write."""
    from chrgd import worker as worker_mod
    from chrgd.worker import RESEARCH_KINDS, VIDEO_KINDS, Worker

    settings.db_path = tmp_path / "t.db"
    in_flight = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow(store_, settings_, job):
        with lock:
            in_flight["now"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        time.sleep(0.15)
        with lock:
            in_flight["now"] -= 1
        return {}

    original = dict(worker_mod._HANDLERS)
    worker_mod._HANDLERS["build"] = slow
    try:
        store.create_job("build")
        store.create_job("build")
        lanes = [
            Worker(settings, poll_interval=0.02,
                   exclude=RESEARCH_KINDS | VIDEO_KINDS,
                   name=f"fast-{i}", recover_on_start=False)
            for i in range(2)
        ]
        for w in lanes:
            w.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            if all(j["status"] == "COMPLETED" for j in store.list_jobs()):
                break
            time.sleep(0.02)
        for w in lanes:
            w.stop()
    finally:
        worker_mod._HANDLERS.clear()
        worker_mod._HANDLERS.update(original)

    jobs = store.list_jobs()
    assert [j["status"] for j in jobs] == ["COMPLETED", "COMPLETED"]
    assert in_flight["peak"] == 2, "the second job waited for the first"
    # Atomically claimed: neither job was picked up twice.
    assert all(j["attempts"] == 1 for j in jobs)


# --- the primitives -----------------------------------------------------------


def test_run_all_returns_results_in_submission_order():
    def slow(n):
        return lambda: (time.sleep(0.03 - n * 0.01), n)[1]

    assert run_all([slow(0), slow(1), slow(2)], workers=3) == [0, 1, 2]


def test_run_all_propagates_the_first_failure():
    calls = []

    def boom():
        raise ValueError("nope")

    def ok():
        calls.append(1)
        return 1

    with pytest.raises(ValueError, match="nope"):
        run_all([boom, ok, ok], workers=3)
    # The others still ran to completion rather than being abandoned mid-call.
    assert len(calls) == 2


def test_workers_never_exceed_the_work_or_the_cap():
    assert workers_for(8, 2) == 2
    assert workers_for(100, 50) == 8   # the hard cap
    assert workers_for(0, 5) == 1
    assert workers_for(4, 0) == 1


def test_the_openai_client_is_shared_per_credential(monkeypatch):
    from chrgd import llm

    made = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            made.append(kwargs)

    import sys
    import types

    module = types.ModuleType("openai")
    module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    llm.reset_clients()
    try:
        a = llm.openai_client("k", "https://api", timeout=30)
        b = llm.openai_client("k", "https://api", timeout=30)
        c = llm.openai_client("other", "https://api", timeout=30)
        assert a is b               # same credential, one connection pool
        assert c is not a           # a different key gets its own
        assert len(made) == 2
    finally:
        llm.reset_clients()


def test_the_learning_corpus_is_bounded_and_light(store):
    from chrgd.models import Idea as I

    for i in range(12):
        store.add_idea(I(idea_id=f"G-{i:04d}", concept_note=f"c{i}",
                         caption="a caption nobody reads here"))
        store.set_metrics(f"G-{i:04d}", {"views": i, "rating": "hit"})

    assert len(store.ideas_with_metrics(limit=5)) == 5
    assert len(store.ideas_with_metrics(limit=None)) == 12
    # Newest first, and without dragging along columns the maths never reads.
    rows = store.ideas_with_metrics(limit=3)
    assert [r.idea_id for r in rows] == ["G-0011", "G-0010", "G-0009"]
    assert all(r.caption is None for r in rows)
    assert all(r.metrics_json for r in rows)
