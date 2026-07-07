"""Tests for the Create & Schedule journeys (all offline).

Covers the pipeline extensions (hook options, facts→angles, targeted revise,
build-one), the per-slide image work (variants, free recompose, style
prompts), the scheduling endpoints, and the calendar/export-range API.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from chrgd.brand import load_brand
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.models import Idea, Status
from chrgd.pipeline import (
    LLMResult,
    build_single_idea,
    build_user_message,
    creation_prefs,
    generate_angles,
    revise_post,
)
from chrgd.webapp import create_app


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "t.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
        CHRGD_WEB_USERNAME="admin",
        CHRGD_WEB_PASSWORD="s3cret",
        CHRGD_SECRET_KEY="test-secret-key",
    )


@pytest.fixture()
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


@pytest.fixture()
def client(settings):
    c = TestClient(create_app(settings))
    c.post("/login", data={"username": "admin", "password": "s3cret"})
    return c


def good_post(**over):
    post = {
        "post_type": "carousel",
        "hook": "hook A",
        "hook_options": ["hook A", "hook B", "hook C"],
        "slides": [
            {"headline": f"h{i}", "supporting": "s", "image_prompt": "gym scene",
             "visual_intent": "v"}
            for i in range(5)
        ],
        "caption": "cap",
        "comment_trigger": "which are you?",
        "hashtags": ["#gym"],
        "route": {
            "mechanic": "identity_exposure",
            "visual_engine": "flash_photo",
            "primary_goal": "shares",
            "build_note": "n",
            "qa": {
                "hook": 9, "swipe_loop": 9, "identity_recognition": 9,
                "group_chat_share": 9, "comment_fight": 6, "saveability": 7,
                "visual_originality": 9, "dopamine_density": 8, "clarity": 9,
                "layout_safety": 9, "claim_safety": 9, "overall": 9,
            },
        },
    }
    post.update(over)
    return post


class FakeChat:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        content = self.payloads.pop(0)
        if not isinstance(content, str):
            content = json.dumps(content)
        return LLMResult(content=content, prompt_tokens=100, completion_tokens=100)


# --- pipeline extensions ------------------------------------------------------


def test_hook_options_persist_in_route(settings, store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    result = build_single_idea(
        store, settings, "G-0001", client=FakeChat([good_post()])
    )
    assert result.status is Status.done
    idea = store.get_idea("G-0001")
    route = json.loads(idea.route_json)
    assert route["hook_options"] == ["hook A", "hook B", "hook C"]


def test_creation_prefs_survive_build(settings, store):
    prefs = {"style": "gritty", "mechanic_lock": {"name": "Myth vs fact", "skeleton": ["a"] * 5}}
    store.add_idea(
        Idea(idea_id="G-0001", concept_note="x", route_json=json.dumps(prefs))
    )
    build_single_idea(store, settings, "G-0001", client=FakeChat([good_post()]))
    route = json.loads(store.get_idea("G-0001").route_json)
    assert route["style"] == "gritty"
    assert route["mechanic_lock"]["name"] == "Myth vs fact"


def test_mechanic_lock_constrains_user_message():
    idea = Idea(
        idea_id="G-0001",
        concept_note="x",
        route_json=json.dumps(
            {"mechanic_lock": {"name": "5 mistakes", "skeleton": ["one", "two", "three", "four", "five"]}}
        ),
    )
    msg = build_user_message(idea)
    assert "'5 mistakes'" in msg
    assert "1. one" in msg and "5. five" in msg


def test_creation_prefs_ignores_engine_route():
    idea = Idea(idea_id="G", concept_note="x", route_json=json.dumps({"qa": {"overall": 9}}))
    assert creation_prefs(idea) == {}


def test_build_single_idea_flags_review_on_qa_miss(settings, store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    bad = good_post()
    bad["route"]["qa"]["overall"] = 5  # both attempts fail
    result = build_single_idea(
        store, settings, "G-0001", client=FakeChat([bad, bad])
    )
    assert result.status is Status.review
    assert store.get_idea("G-0001").status is Status.review


def test_generate_angles(settings):
    payload = {
        "angles": [
            {"title": "Myth-bust", "mechanic": "rage_agreement", "hook": "h",
             "concept_note": "bust the toning myth", "fact": "toning is fat loss"},
            {"title": "Hot take", "concept_note": "another take"},
        ]
    }
    result = generate_angles("toning is fat loss", settings, client=FakeChat([payload]))
    assert [a.title for a in result.angles] == ["Myth-bust", "Hot take"]
    assert result.spend_usd > 0


def test_revise_post_sends_current_copy(settings):
    idea = Idea(
        idea_id="G-0001",
        concept_note="x",
        hook="old hook",
        slides_json=json.dumps(good_post()["slides"]),
        caption="old cap",
        hashtags=json.dumps(["#gym"]),
    )
    fake = FakeChat([good_post(hook="punchier")])
    post, spend = revise_post(idea, "saveability", settings, client=fake)
    assert post.hook == "punchier"
    assert spend > 0
    _, user = fake.calls[0]
    assert "saveability" in user and "old hook" in user


def test_blank_base_url_env_var_never_reaches_sdk(settings, monkeypatch):
    """Regression: systemd EnvironmentFile exports a blank OPENAI_BASE_URL=
    line as a real empty env var; the SDK then used '' as the URL and every
    engine call failed with 'missing http:// protocol'. The client must
    always receive an explicit, valid base_url."""
    from chrgd.pipeline import OpenAIChatClient

    monkeypatch.setenv("OPENAI_BASE_URL", "")
    empty = Settings(OPENAI_API_KEY="sk-test", OPENAI_BASE_URL="")
    assert empty.get_openai_base_url() == "https://api.openai.com/v1"
    client = OpenAIChatClient(empty)
    assert str(client._client.base_url).startswith("https://api.openai.com")

    # A real override still wins.
    routed = Settings(OPENAI_API_KEY="sk-test", OPENAI_BASE_URL="https://gw.example/v1")
    assert routed.get_openai_base_url() == "https://gw.example/v1"


# --- worker job kinds ---------------------------------------------------------


def test_worker_render_slide_job(settings, store):
    from chrgd.worker import Worker, enqueue_render, enqueue_render_slide

    slides = good_post()["slides"]
    store.add_idea(Idea(idea_id="G-0001", concept_note="x", slides_json=json.dumps(slides)))
    worker = Worker(settings)

    # Full render first (dry) — writes asset paths + backgrounds.
    full = enqueue_render(store, "G-0001", dry_run=True)
    worker.run_once()
    assert store.get_job(full)["status"] == "COMPLETED"

    job_id = enqueue_render_slide(store, "G-0001", slide=2, dry_run=True)
    assert worker.run_once() == job_id
    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED"
    result = json.loads(job["result_json"])
    assert result["path"].endswith("slide_3.jpg")


def test_worker_revise_job_saves_post(settings, store, monkeypatch):
    from chrgd import worker as worker_mod
    from chrgd.models import Post
    from chrgd.worker import Worker, enqueue_revise

    store.add_idea(
        Idea(
            idea_id="G-0001",
            concept_note="x",
            slides_json=json.dumps(good_post()["slides"]),
            route_json=json.dumps({"style": "gritty"}),
        )
    )
    store.mark_review("G-0001", {"hook": "weak"})

    import chrgd.pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod,
        "revise_post",
        lambda idea, focus, settings, client=None: (
            Post.model_validate(good_post(hook="revised")), 0.01
        ),
    )
    job_id = enqueue_revise(store, "G-0001", focus="saveability")
    Worker(settings).run_once()
    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED", job["error"]
    idea = store.get_idea("G-0001")
    assert idea.hook == "revised"
    assert idea.status is Status.done  # revision passed QA
    assert json.loads(idea.route_json)["style"] == "gritty"  # prefs survive


def test_worker_build_one_llm_failure_fails_job(settings, store, monkeypatch):
    """Regression: a swallowed LLM error must FAIL the job, not complete it.

    Before this fix the job completed with the error tucked inside its result,
    and the create journey spinner span forever with nothing to show.
    """
    import chrgd.pipeline as pipeline_mod
    from chrgd.pipeline import LLMError
    from chrgd.worker import Worker, enqueue_build_one

    settings.openai_api_key = "sk-test"
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    monkeypatch.setattr(
        pipeline_mod, "OpenAIChatClient", lambda s: FakeChat([])
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_pipeline_for_idea",
        lambda *a, **k: (_ for _ in ()).throw(LLMError("quota exceeded")),
    )
    job_id = enqueue_build_one(store, "G-0001")
    Worker(settings).run_once()
    job = store.get_job(job_id)
    assert job["status"] == "ERROR"
    assert "quota exceeded" in job["error"]
    # The idea is released for a retry, not stuck in processing.
    assert store.get_idea("G-0001").status is Status.queued


def test_build_single_idea_reports_progress(settings, store):
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    bad = good_post()
    bad["route"]["qa"]["overall"] = 5
    seen = []
    build_single_idea(
        store, settings, "G-0001",
        client=FakeChat([bad, good_post()]),
        on_progress=lambda pct, note: seen.append((pct, note)),
    )
    assert seen[0][0] == 15 and "six stages" in seen[0][1]
    assert seen[1][0] == 60 and "rewrite" in seen[1][1]


def test_detail_surfaces_note_and_only_latest_error(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        # An old failure followed by a newer successful job → no error shown.
        old = store.create_job("build_one", idea_id="G-0001")
        store.update_job(old, status="ERROR", error="boom")
        newer = store.create_job("build_one", idea_id="G-0001")
        store.update_job(newer, status="COMPLETED")
    d = client.get("/api/ideas/G-0001/detail").json()
    assert d["last_error"] is None

    with Store(settings.db_path) as store:
        running = store.create_job("render", idea_id="G-0001")
        store.update_job(
            running, status="PROCESSING", progress=40,
            result_json=json.dumps({"note": "slide 2 of 5 done"}),
        )
    d = client.get("/api/ideas/G-0001/detail").json()
    assert d["active_jobs"]["render"]["note"] == "slide 2 of 5 done"
    assert d["active_jobs"]["render"]["progress"] == 40

    with Store(settings.db_path) as store:
        store.update_job(running, status="ERROR", error="image api down")
    d = client.get("/api/ideas/G-0001/detail").json()
    assert d["last_error"]["error"] == "image api down"


# --- images: variants, recompose, style prompts --------------------------------


def make_built_idea(idea_id="G-0001", style=None, render_mode=None):
    route = {}
    if style:
        route["style"] = style
    if render_mode:
        route["render_mode"] = render_mode
    return Idea(
        idea_id=idea_id,
        concept_note="x",
        slides_json=json.dumps(good_post()["slides"]),
        route_json=json.dumps(route) if route else None,
    )


def test_render_saves_backgrounds_and_variants(settings):
    from chrgd.images import list_variants, render_carousel

    idea = make_built_idea()
    result = render_carousel(idea, settings, dry_run=True)
    out = settings.output_dir / "G-0001"
    n_first = load_brand().generation.variants_first
    assert (out / "bg_1.jpg").exists() and (out / "bg_5.jpg").exists()
    assert (out / "bg_1_a.jpg").exists()
    variants = list_variants(idea, settings)
    assert len(variants[0]) == n_first
    assert 1 not in variants  # slides 2-5 have no variants
    assert len(result.paths) == 5


def test_recompose_is_free_and_updates_slide(settings):
    from chrgd.images import recompose_slide, render_carousel

    idea = make_built_idea(render_mode="overlay")
    render_carousel(idea, settings, dry_run=True)
    # Edit the copy, then re-lay text without regenerating.
    slides = json.loads(idea.slides_json)
    slides[2]["headline"] = "new headline"
    idea.slides_json = json.dumps(slides)
    result = recompose_slide(idea, 2, settings)
    assert result.generated == 0 and result.spend_usd == 0
    assert result.path.endswith("slide_3.jpg")


def test_recompose_without_background_errors(settings):
    from chrgd.images import ImageError, recompose_slide

    with pytest.raises(ImageError):
        recompose_slide(make_built_idea(render_mode="overlay"), 0, settings)


def test_recompose_refused_for_ai_designed_slides(settings):
    from chrgd.images import ImageError, recompose_slide, render_carousel

    idea = make_built_idea()  # default mode: ai_design
    render_carousel(idea, settings, dry_run=True)
    with pytest.raises(ImageError, match="regenerate"):
        recompose_slide(idea, 0, settings)


def test_render_mode_default_and_override():
    from chrgd.images import render_mode_for_idea

    brand = load_brand()
    assert render_mode_for_idea(make_built_idea(), brand) == "ai_design"
    assert (
        render_mode_for_idea(make_built_idea(render_mode="overlay"), brand)
        == "overlay"
    )


def test_compose_design_prompt_places_copy_as_typography():
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    brand = load_brand()
    slide = Slide(
        headline="THE MYTH DIES HERE",
        supporting="toning is just fat loss",
        image_prompt="brutalist poster, torn paper collage, huge condensed type",
    )
    prompt = compose_design_prompt(slide, brand, "editorial")
    assert "TEXT TO PLACE ON IMAGE:" in prompt
    assert "Headline text: THE MYTH DIES HERE" in prompt
    assert "Supporting text: toning is just fat loss" in prompt
    assert "brutalist poster" in prompt
    assert brand.styles["editorial"].prompt in prompt
    assert "Use exactly the text provided above." in prompt
    # The overlay-mode "no text" clause must NOT leak into design mode.
    assert "no text, no words" not in prompt.lower()


def test_ai_design_render_uses_model_output_verbatim(settings, monkeypatch):
    from PIL import Image as PILImage

    from chrgd import images

    settings.openai_api_key = "sk-test"
    marker = PILImage.new("RGB", (1080, 1350), (12, 200, 34))
    prompts = []

    def fake_generate(prompt, *a, **k):
        prompts.append(prompt)
        return marker.copy()

    monkeypatch.setattr(images, "_generate_background", fake_generate)
    idea = make_built_idea()  # ai_design default
    result = images.render_slide(idea, 2, settings, dry_run=False)
    # Prompt was a full design prompt with the copy in it…
    assert "TEXT TO PLACE ON IMAGE:" in prompts[0]
    # …and the finished slide is the model's design, not overlaid by code
    # (centre pixel matches the marker within JPEG tolerance — an overlay's
    # contrast panel would sit exactly there and darken it heavily).
    out = PILImage.open(result.path)
    px = out.getpixel((540, 675))
    assert all(abs(a - b) <= 3 for a, b in zip(px, (12, 200, 34))), px


def test_pick_variant_promotes_canonical(settings):
    from chrgd.images import pick_variant, render_carousel

    idea = make_built_idea()
    render_carousel(idea, settings, dry_run=True)
    out = settings.output_dir / "G-0001"
    picked = pick_variant(idea, 0, "b", settings)
    assert picked.endswith("slide_1.jpg")
    assert (out / "slide_1.jpg").read_bytes() == (out / "slide_1_b.jpg").read_bytes()
    assert (out / "bg_1.jpg").read_bytes() == (out / "bg_1_b.jpg").read_bytes()


def test_compose_image_prompt_layers():
    from chrgd.images import compose_image_prompt

    brand = load_brand()
    prompt = compose_image_prompt("a squat rack at night", brand, "gritty")
    assert "a squat rack at night" in prompt
    assert brand.styles["gritty"].prompt in prompt
    assert "no text" in prompt.lower()
    # Unknown style: no crash, no style block.
    assert "squat rack" in compose_image_prompt("a squat rack", brand, None)


# --- create journey endpoints ---------------------------------------------------


def test_create_start_idea_mode(client, settings):
    r = client.post(
        "/api/create/start",
        data={"mode": "idea", "text": "gym idea", "style": "gritty",
              "scheduled_for": "2027-01-05T18:00"},
    )
    body = r.json()
    assert r.status_code == 200 and body["job_id"]
    with Store(settings.db_path) as store:
        idea = store.get_idea(body["idea_id"])
        assert idea.concept_note == "gym idea"
        assert json.loads(idea.route_json)["style"] == "gritty"
        assert idea.scheduled_for.isoformat().startswith("2027-01-05T18:00")
        job = store.get_job(body["job_id"])
        assert job["kind"] == "build_one" and job["idea_id"] == idea.idea_id


def test_create_start_blank_manual(client, settings):
    r = client.post(
        "/api/create/start",
        data={"mode": "blank", "manual": "true", "mechanic": "myth_fact"},
    )
    body = r.json()
    assert body["manual"] is True
    with Store(settings.db_path) as store:
        idea = store.get_idea(body["idea_id"])
        assert idea.status is Status.review  # never ships without approve
        assert len(json.loads(idea.slides_json)) == 5
        assert json.loads(idea.route_json)["mechanic_lock"]["name"] == "Myth vs fact"


def test_create_start_facts_then_pick_angle(client, settings):
    r = client.post("/api/create/start", data={"mode": "facts", "text": "a fact"})
    job_id = r.json()["job_id"]
    with Store(settings.db_path) as store:
        assert store.get_job(job_id)["kind"] == "angles"
        store.update_job(
            job_id,
            status="COMPLETED",
            result_json=json.dumps(
                {"angles": [{"title": "T", "concept_note": "the brief",
                             "pain_point": "pp", "core_tension": "ct"}]}
            ),
        )
    r2 = client.post(f"/api/create/angle/{job_id}", data={"index": 0, "style": "meme"})
    idea_id = r2.json()["idea_id"]
    with Store(settings.db_path) as store:
        idea = store.get_idea(idea_id)
        assert idea.concept_note == "the brief"
        assert idea.pain_point == "pp"
        assert idea.content_category == "fact"
        assert json.loads(idea.route_json)["style"] == "meme"


def test_create_start_rejects_bad_input(client):
    assert client.post("/api/create/start", data={"mode": "idea", "text": " "}).status_code == 400
    assert client.post("/api/create/start", data={"mode": "nope"}).status_code == 400
    assert (
        client.post(
            "/api/create/start",
            data={"mode": "blank", "mechanic": "not_a_mechanic"},
        ).status_code
        == 400
    )


def test_pick_hook_keeps_status(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        store.mark_review("G-0001", {"hook": "old"})
    r = client.post("/api/ideas/G-0001/hook", data={"hook": "new hook"})
    assert r.json()["hook"] == "new hook"
    with Store(settings.db_path) as store:
        idea = store.get_idea("G-0001")
        assert idea.hook == "new hook"
        assert idea.status is Status.review


def test_detail_endpoint_shape(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        store.save_build(
            "G-0001",
            {
                "hook": "h",
                "slides_json": json.dumps(good_post()["slides"]),
                "route_json": json.dumps(
                    {"qa": {"overall": 9}, "hook_options": ["a", "b"], "style": "gritty"}
                ),
            },
        )
    d = client.get("/api/ideas/G-0001/detail").json()
    assert d["hook_options"] == ["a", "b"]
    assert d["qa"] == {"overall": 9}
    assert d["style"] == "gritty"
    assert d["status"] == "done"
    assert d["active_jobs"] == {}
    assert client.get("/api/ideas/NOPE/detail").status_code == 404


def test_create_start_render_mode_stored(client, settings):
    r = client.post(
        "/api/create/start",
        data={"mode": "idea", "text": "x", "render_mode": "overlay"},
    )
    idea_id = r.json()["idea_id"]
    with Store(settings.db_path) as store:
        assert json.loads(store.get_idea(idea_id).route_json)["render_mode"] == "overlay"
    d = client.get(f"/api/ideas/{idea_id}/detail").json()
    assert d["render_mode"] == "overlay"
    assert (
        client.post(
            "/api/create/start", data={"mode": "idea", "text": "x", "render_mode": "nope"}
        ).status_code
        == 400
    )


def test_slide_endpoints_roundtrip(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(make_built_idea(render_mode="overlay"))
    # Render synchronously (dry) so backgrounds exist.
    client.post("/api/render/G-0001", params={"dry_run": True})
    # Free recompose works.
    r = client.post("/api/ideas/G-0001/slides/2/recompose")
    assert r.status_code == 200 and r.json()["path"].endswith("slide_3.jpg")
    # Variant pick works for slide 1.
    r = client.post("/api/ideas/G-0001/slides/0/variant", data={"variant": "b"})
    assert r.status_code == 200
    # Unknown variant → 400.
    assert (
        client.post("/api/ideas/G-0001/slides/0/variant", data={"variant": "z"}).status_code
        == 400
    )
    # Regenerate enqueues a render_slide job.
    r = client.post("/api/ideas/G-0001/slides/1/regenerate", params={"dry_run": True})
    assert r.json()["kind"] == "render_slide"


# --- UK moments radar -----------------------------------------------------------

MOMENTS_PAYLOAD = {
    "moments": [
        {
            "title": "Heatwave hitting Sat-Sun, 32C",
            "emoji": "🥵",
            "category": "weather",
            "when": "this weekend",
            "why": "whole country melting, everyone talking about it",
            "decay_speed": "days",
            "angles": [
                {"type": "advice", "title": "Training in the heat without dying",
                 "hook": "32C and leg day. here's how.", "concept_note": "practical heat tips"},
                {"type": "funny", "title": "Gym in a heatwave starter pack",
                 "hook": "the fan queue", "concept_note": "heatwave gym chaos"},
                {"type": "tiein", "title": "Hydration + electrolytes 101",
                 "hook": "", "concept_note": "electrolytes explainer, observational"},
            ],
        },
        {
            "title": "England v Mexico, 2am kick-off Thu",
            "emoji": "⚽",
            "category": "sport",
            "when": "Thu",
            "why": "half the UK staying up for it",
            "decay_speed": "days",
            "angles": [
                {"type": "advice", "title": "Surviving the 2am kick-off",
                 "hook": "staying up for the game? read this", "concept_note": "sleep/caffeine timing tips"}
            ],
        },
    ]
}


class FakeSearch:
    def __init__(self, payload):
        self.payload = payload

    def search(self, system, user):
        self.system = system
        return json.dumps(self.payload)


def test_scout_moments_parses_and_ranks(settings):
    from chrgd.trends import scout_moments

    fake = FakeSearch(MOMENTS_PAYLOAD)
    result = scout_moments(settings, client=fake)
    assert [m.category for m in result.moments] == ["weather", "sport"]
    assert result.moments[0].angles[0].type == "advice"
    assert "COLLECTIVELY" in fake.system  # the radar brief, not the gym scout


def test_worker_discover_job_and_dedupe(settings, store, monkeypatch):
    import chrgd.trends as trends_mod
    from chrgd.trends import MomentsResult
    from chrgd.worker import Worker, enqueue_discover

    seen_kinds = []

    def fake_scout(s, kind="moments", count=6, client=None):
        seen_kinds.append(kind)
        return MomentsResult.model_validate(MOMENTS_PAYLOAD)

    monkeypatch.setattr(trends_mod, "scout_discover", fake_scout)
    a = enqueue_discover(store, "moments")
    b = enqueue_discover(store, "moments")
    assert a == b  # one scan per lane at a time
    c = enqueue_discover(store, "evergreen")
    assert c != a  # separate lane, separate scan
    worker = Worker(settings)
    worker.run_once()
    worker.run_once()
    assert store.get_job(a)["status"] == "COMPLETED"
    assert store.get_job(c)["status"] == "COMPLETED"
    assert seen_kinds == ["moments", "evergreen"]
    assert len(json.loads(store.get_job(a)["result_json"])["moments"]) == 2
    with pytest.raises(ValueError):
        enqueue_discover(store, "nope")


def test_moments_api_and_use_flow(client, settings):
    # No scan yet.
    empty = client.get("/api/moments").json()
    assert empty["moments"] == [] and empty["scanning"] is False

    # Enqueue → reported as scanning.
    job_id = client.post("/api/jobs/moments").json()["job_id"]
    assert client.get("/api/moments").json()["scanning"] is True

    # Simulate the worker completing the scan.
    with Store(settings.db_path) as store:
        store.update_job(
            job_id, status="COMPLETED", result_json=json.dumps(MOMENTS_PAYLOAD)
        )
    data = client.get("/api/moments").json()
    assert data["job_id"] == job_id
    assert [m["title"] for m in data["moments"]][1].startswith("England v Mexico")
    assert data["age_hours"] is not None and data["age_hours"] < 1

    # One tap on an angle → seeded idea + build job.
    r = client.post(
        f"/api/moments/{job_id}/use",
        data={"moment": 1, "angle": 0, "style": "gritty", "render_mode": "ai_design"},
    )
    body = r.json()
    with Store(settings.db_path) as store:
        idea = store.get_idea(body["idea_id"])
        assert "England v Mexico" in idea.concept_note
        assert "staying up for the game" in idea.concept_note  # hook carried in
        assert idea.content_category == "moment"
        assert idea.decay_speed is not None and idea.decay_speed.value == "days"
        assert idea.learning_tag == "moment:sport"
        route = json.loads(idea.route_json)
        assert route["style"] == "gritty" and route["render_mode"] == "ai_design"
        job = store.get_job(body["job_id"])
        assert job["kind"] == "build_one" and job["idea_id"] == idea.idea_id

    # Bad indices → 400, unknown job → 404.
    assert client.post(f"/api/moments/{job_id}/use", data={"moment": 9, "angle": 0}).status_code == 400
    assert client.post("/api/moments/99999/use", data={"moment": 0, "angle": 0}).status_code == 404


def test_evergreen_lane_and_develop_from_moment(client, settings):
    # Lanes are cached separately.
    assert client.get("/api/moments", params={"kind": "nope"}).status_code == 400
    ever_job = client.post("/api/jobs/moments", data={"kind": "evergreen"}).json()["job_id"]
    with Store(settings.db_path) as store:
        store.update_job(
            ever_job, status="COMPLETED", result_json=json.dumps(MOMENTS_PAYLOAD)
        )
    assert client.get("/api/moments").json()["moments"] == []  # moments lane untouched
    ever = client.get("/api/moments", params={"kind": "evergreen"}).json()
    assert len(ever["moments"]) == 2

    # Using an evergreen card with develop=true starts a concept round.
    r = client.post(
        f"/api/moments/{ever_job}/use",
        data={"moment": 0, "angle": 0, "develop": "true"},
    )
    body = r.json()
    assert body["develop"] is True
    with Store(settings.db_path) as store:
        job = store.get_job(body["job_id"])
        assert job["kind"] == "concept" and job["idea_id"] == body["idea_id"]


# --- concept development ("develop it with me") -----------------------------------

BRIEF = {
    "angle": "the 2am kick-off is a training problem",
    "hook_direction": "open on the alarm going off",
    "outline": ["hook", "the problem", "the science", "the plan", "cta"],
    "tone": "dry, mate-to-mate",
    "visual_direction": "dark kitchen at 2am, phone glow",
}


def test_develop_concept_iterates_with_feedback(settings):
    from chrgd.pipeline import develop_concept

    idea = Idea(
        idea_id="G-0001", concept_note="england game staying up",
        route_json=json.dumps({"concept_brief": BRIEF}),
    )
    fake = FakeChat([dict(BRIEF, angle="sharper angle")])
    brief, spend = develop_concept(
        idea, settings, feedback="make it about pre-work training", client=fake
    )
    assert brief.angle == "sharper angle"
    assert spend > 0
    _, user = fake.calls[0]
    assert "EDITOR FEEDBACK" in user and "pre-work training" in user
    assert "evolve it, don't start over" in user  # prior brief included
    assert BRIEF["angle"] in user


def test_worker_concept_job_saves_brief(settings, store, monkeypatch):
    import chrgd.pipeline as pipeline_mod
    from chrgd.pipeline import ConceptBrief
    from chrgd.worker import Worker, enqueue_concept

    store.add_idea(
        Idea(idea_id="G-0001", concept_note="x",
             route_json=json.dumps({"style": "gritty"}))
    )
    monkeypatch.setattr(
        pipeline_mod, "develop_concept",
        lambda idea, settings, feedback="", client=None: (
            ConceptBrief.model_validate(BRIEF), 0.01
        ),
    )
    job_id = enqueue_concept(store, "G-0001", feedback="go")
    assert enqueue_concept(store, "G-0001") == job_id  # no double-queue
    Worker(settings).run_once()
    job = store.get_job(job_id)
    assert job["status"] == "COMPLETED", job["error"]
    route = json.loads(store.get_idea("G-0001").route_json)
    assert route["concept_brief"]["angle"] == BRIEF["angle"]
    assert route["style"] == "gritty"  # untouched


def test_build_user_message_follows_brief():
    idea = Idea(
        idea_id="G-0001", concept_note="x",
        route_json=json.dumps({"concept_brief": BRIEF}),
    )
    msg = build_user_message(idea)
    assert "CONCEPT BRIEF" in msg
    assert BRIEF["angle"] in msg
    assert "slide 3: the science" in msg


def test_create_start_develop_flag(client, settings):
    r = client.post(
        "/api/create/start",
        data={"mode": "idea", "text": "x", "develop": "true"},
    )
    body = r.json()
    assert body["develop"] is True
    with Store(settings.db_path) as store:
        job = store.get_job(body["job_id"])
        assert job["kind"] == "concept" and job["idea_id"] == body["idea_id"]


def test_brief_edit_develop_and_detail(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(
            Idea(idea_id="G-0001", concept_note="x",
                 route_json=json.dumps({"concept_brief": BRIEF, "style": "meme"}))
        )
    # Direct edits save into the brief.
    r = client.post(
        "/api/ideas/G-0001/brief",
        data={"angle": "my edited angle", "outline_0": "my hook",
              "outline_1": "b", "outline_2": "c", "outline_3": "d", "outline_4": "e"},
    )
    assert r.json()["brief"]["angle"] == "my edited angle"
    d = client.get("/api/ideas/G-0001/detail").json()
    assert d["concept_brief"]["angle"] == "my edited angle"
    assert d["concept_brief"]["outline"][0] == "my hook"
    assert d["concept_brief"]["tone"] == BRIEF["tone"]  # untouched fields kept
    # Another round enqueues a concept job carrying the feedback.
    r = client.post("/api/ideas/G-0001/develop", data={"feedback": "funnier"})
    with Store(settings.db_path) as store:
        job = store.get_job(r.json()["job_id"])
        assert job["kind"] == "concept"
        assert json.loads(job["params_json"])["feedback"] == "funnier"


# --- scheduling + calendar -------------------------------------------------------


def test_schedule_set_and_clear(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    r = client.post("/api/ideas/G-0001/schedule", data={"when": "2027-03-04T18:30"})
    assert r.json()["scheduled_for"].startswith("2027-03-04T18:30")
    r = client.post("/api/ideas/G-0001/schedule", data={"when": ""})
    assert r.json()["scheduled_for"] is None
    assert client.post("/api/ideas/G-0001/schedule", data={"when": "gibberish"}).status_code == 400


def test_calendar_groups_days_and_tray(client, settings):
    slides = json.dumps(good_post()["slides"])
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="scheduled one", slides_json=slides))
        store.set_status("G-0001", Status.done)
        store.set_schedule("G-0001", datetime(2027, 3, 4, 18, 0))
        store.add_idea(Idea(idea_id="G-0002", concept_note="tray one", slides_json=slides))
        store.set_status("G-0002", Status.done)
        store.add_idea(Idea(idea_id="G-0003", concept_note="seed — not built"))
    data = client.get(
        "/api/calendar",
        params={"date_from": "2027-03-01T00:00:00", "date_to": "2027-04-01T00:00:00"},
    ).json()
    assert [c["idea_id"] for c in data["days"]["2027-03-04"]] == ["G-0001"]
    tray_ids = [c["idea_id"] for c in data["tray"]]
    assert "G-0002" in tray_ids and "G-0003" not in tray_ids


def test_calendar_flags_stale_topical_ideas(client, settings):
    slides = json.dumps(good_post()["slides"])
    old = datetime.now(timezone.utc) - timedelta(days=10)
    with Store(settings.db_path) as store:
        store.add_idea(
            Idea(idea_id="G-0001", concept_note="old trend", slides_json=slides,
                 decay_speed="days", created_at=old)
        )
        store.set_status("G-0001", Status.done)
    data = client.get("/api/calendar").json()
    card = [c for c in data["tray"] if c["idea_id"] == "G-0001"][0]
    assert card["stale"] is True


def test_export_range_respects_window_and_user_schedule(client, settings, tmp_path):
    from PIL import Image as PILImage

    slides = json.dumps(good_post()["slides"])
    settings.ensure_dirs()

    def add_rendered(store, idea_id, when):
        out = settings.output_dir / idea_id
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(5):
            p = out / f"slide_{i + 1}.jpg"
            PILImage.new("RGB", (10, 12)).save(p, "JPEG")
            paths.append(str(p))
        store.add_idea(Idea(idea_id=idea_id, concept_note=idea_id, slides_json=slides,
                            caption="c", hashtags=json.dumps(["#g"])))
        store.save_build(idea_id, {"slides_json": slides})
        store.save_asset_paths(idea_id, paths)
        if when:
            store.set_schedule(idea_id, when)

    with Store(settings.db_path) as store:
        add_rendered(store, "G-0001", datetime(2027, 3, 2, 18, 0))   # in window
        add_rendered(store, "G-0002", datetime(2027, 4, 20, 18, 0))  # outside
        add_rendered(store, "G-0003", None)                           # unscheduled

    r = client.post(
        "/api/export/range", data={"date_from": "2027-03-01", "date_to": "2027-03-07"}
    )
    body = r.json()
    assert body["exported"] == ["G-0001"]
    with Store(settings.db_path) as store:
        assert store.get_idea("G-0001").exported_at is not None
        assert store.get_idea("G-0002").exported_at is None
        # The user's chosen slot is what lands in the CSV.
        csv_text = open(body["csv_path"], encoding="utf-8").read()
        assert "02/03/2027 18:00" in csv_text
