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
    notes = [n for _, n in seen]
    # The queue narrates the real call lifecycle, both attempts.
    assert "composing the build instructions" in notes[0]
    assert any("request sent" in n and "waiting" in n for n in notes)
    assert any("response received" in n and "QA" in n for n in notes)
    assert any("stronger rewrite" in n for n in notes)


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


def test_render_saves_backgrounds_one_image_per_slide(settings):
    from chrgd.images import list_variants, render_carousel

    idea = make_built_idea()
    result = render_carousel(idea, settings, dry_run=True)
    out = settings.output_dir / "G-0001"
    assert load_brand().generation.variants_first == 1  # one image per slide
    assert (out / "bg_1.jpg").exists() and (out / "bg_5.jpg").exists()
    assert not (out / "bg_1_a.jpg").exists()  # no default variant pairs
    assert list_variants(idea, settings) == {}
    assert len(result.paths) == 5


def test_explicit_variants_still_work(settings):
    from chrgd.images import list_variants, render_slide

    idea = make_built_idea()
    result = render_slide(idea, 0, settings, dry_run=True, variants=2)
    assert len(result.variant_paths) == 2
    assert len(list_variants(idea, settings)[0]) == 2


def test_render_slide_narrates_each_step(settings, monkeypatch):
    from PIL import Image as PILImage

    from chrgd import images

    settings.openai_api_key = "sk-test"
    monkeypatch.setattr(
        images, "_generate_background",
        lambda *a, **k: PILImage.new("RGB", (100, 150)),
    )
    seen = []
    images.render_slide(
        make_built_idea(), 2, settings, dry_run=False, notify=seen.append
    )
    assert any("request sent" in n and "waiting" in n for n in seen)
    assert any("image received" in n for n in seen)
    assert all(n.startswith("slide 3:") for n in seen)


def test_recompose_always_refused_text_is_baked_by_api(settings):
    from chrgd.images import ImageError, recompose_slide, render_carousel

    # Every slide is AI-designed now: the text is part of the artwork, so a
    # free code-side re-overlay is never valid — copy changes need a regen.
    idea = make_built_idea(render_mode="ai_design")
    render_carousel(idea, settings, dry_run=True)
    with pytest.raises(ImageError, match="regenerate"):
        recompose_slide(idea, 0, settings)


def test_recompose_refused_even_when_overlay_requested(settings):
    from chrgd.images import ImageError, recompose_slide

    # A stored "overlay"/"branded" hint is ignored — API-baked text wins.
    with pytest.raises(ImageError, match="regenerate"):
        recompose_slide(make_built_idea(render_mode="overlay"), 0, settings)


def test_render_mode_is_always_ai_design():
    from chrgd.images import render_mode_for_idea

    brand = load_brand()
    # ai_design is the only mode: ALL image text is generated by the image
    # API and baked into the artwork — never overlaid in code. A stored hint
    # of "overlay"/"branded" is ignored.
    assert render_mode_for_idea(make_built_idea(), brand) == "ai_design"
    assert render_mode_for_idea(make_built_idea(render_mode="overlay"), brand) == "ai_design"
    assert render_mode_for_idea(make_built_idea(render_mode="branded"), brand) == "ai_design"


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
    assert "Use EXACTLY the text provided above" in prompt
    # Edge-safety: the model must be told to keep text off the frame edges.
    assert "margin" in prompt.lower() and "off any edge" in prompt.lower()
    # The overlay-mode "no text" clause must NOT leak into design mode.
    assert "no text, no words" not in prompt.lower()


def test_design_prompt_carries_shared_system_and_continuity():
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    brand = load_brand()
    slides = [
        Slide(headline=f"H{i}", supporting="s", image_prompt="scene",
              visual_intent=f"frame {i} subject", role="hook" if i == 0 else "escalation")
        for i in range(5)
    ]
    design_system = {
        "palette": "acid yellow on charcoal",
        "type_style": "condensed uppercase grotesk",
        "motif": "the same gym bro in a yellow vest",
        "layout": "headline top third, subject centred",
        "evolution": "the palette heats toward red as it escalates",
    }

    # Middle frame: must restate the shared system, reference the previous
    # frame for continuity, carry the evolution beat and a progress indicator.
    mid = compose_design_prompt(
        slides[2], brand, None, slides=slides, index=2, design_system=design_system
    )
    assert "SHARED CAROUSEL DESIGN SYSTEM" in mid
    assert "acid yellow on charcoal" in mid
    assert "the same gym bro in a yellow vest" in mid
    assert "frame 3 of 5" in mid
    assert "frame 1 subject" in mid  # continues from the previous slide (index 1)
    assert "heats toward red" in mid  # evolution beat
    assert "3 of 5" in mid or "dot 3" in mid or "5 dots" in mid or "progress" in mid.lower()

    # Opening frame establishes the world instead of referencing a previous one.
    first = compose_design_prompt(
        slides[0], brand, None, slides=slides, index=0, design_system=design_system
    )
    assert "OPENING frame" in first
    assert "frame 1 of 5" in first


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
    idea = make_built_idea(render_mode="ai_design")
    result = images.render_slide(idea, 2, settings, dry_run=False)
    # Prompt was a full design prompt with the copy in it…
    assert "TEXT TO PLACE ON IMAGE:" in prompts[0]
    # …and the finished slide is the model's design, not overlaid by code
    # (centre pixel matches the marker within JPEG tolerance — an overlay's
    # contrast panel would sit exactly there and darken it heavily).
    out = PILImage.open(result.path)
    px = out.getpixel((540, 675))
    assert all(abs(a - b) <= 3 for a, b in zip(px, (12, 200, 34))), px


def test_render_slide_threads_design_system_from_route(settings, monkeypatch):
    from PIL import Image as PILImage

    from chrgd import images

    settings.openai_api_key = "sk-test"
    prompts = []
    monkeypatch.setattr(
        images, "_generate_background",
        lambda prompt, *a, **k: (prompts.append(prompt), PILImage.new("RGB", (1080, 1350)))[1],
    )
    idea = make_built_idea(render_mode="ai_design")
    route = json.loads(idea.route_json)
    route["design_system"] = {"palette": "cobalt on bone", "motif": "one battered kettlebell"}
    idea.route_json = json.dumps(route)

    images.render_slide(idea, 3, settings, dry_run=False)
    # The real image prompt carried the shared design system + sequence
    # position, so every generated slide belongs to one continuous set.
    assert "SHARED CAROUSEL DESIGN SYSTEM" in prompts[0]
    assert "cobalt on bone" in prompts[0]
    assert "one battered kettlebell" in prompts[0]
    assert "frame 4 of 5" in prompts[0]


def test_pick_variant_promotes_canonical(settings):
    from chrgd.images import pick_variant, render_slide

    idea = make_built_idea()
    render_slide(idea, 0, settings, dry_run=True, variants=2)
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


def test_create_start_render_mode_always_ai_design(client, settings):
    # Even if a legacy "overlay" hint is posted, the effective render mode is
    # forced to ai_design (API-baked text) everywhere it matters.
    r = client.post(
        "/api/create/start",
        data={"mode": "idea", "text": "x", "render_mode": "overlay"},
    )
    idea_id = r.json()["idea_id"]
    d = client.get(f"/api/ideas/{idea_id}/detail").json()
    assert d["render_mode"] == "ai_design"
    assert (
        client.post(
            "/api/create/start", data={"mode": "idea", "text": "x", "render_mode": "nope"}
        ).status_code
        == 400
    )


def test_slide_endpoints_roundtrip(client, settings):
    from chrgd.images import render_slide

    idea = make_built_idea()
    with Store(settings.db_path) as store:
        store.add_idea(idea)
    # Render synchronously (dry) so backgrounds exist.
    client.post("/api/render/G-0001", params={"dry_run": True})
    # Recompose is refused — text is baked into the AI-designed artwork, so a
    # copy change needs a full regenerate, not a free code re-overlay.
    r = client.post("/api/ideas/G-0001/slides/2/recompose")
    assert r.status_code == 400
    # Variant pick works once options exist (explicitly requested — the
    # default is one image per slide).
    render_slide(idea, 0, settings, dry_run=True, variants=2)
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


def test_queue_page_and_retry(client, settings):
    assert "Queue" in client.get("/queue").text
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        job_id = store.create_job(
            "render", idea_id="G-0001", params={"dry_run": True}
        )
        store.update_job(job_id, status="ERROR", error="boom")
    r = client.post(f"/api/jobs/{job_id}/retry")
    body = r.json()
    assert body["kind"] == "render" and body["retried_from"] == job_id
    with Store(settings.db_path) as store:
        new = store.get_job(body["job_id"])
        assert new["status"] == "QUEUED"
        assert json.loads(new["params_json"]) == {"dry_run": True}
        assert new["idea_id"] == "G-0001"
    # Only failed jobs can be retried.
    assert client.post(f"/api/jobs/{body['job_id']}/retry").status_code == 400
    assert client.post("/api/jobs/99999/retry").status_code == 404


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
            "title": "World Cup semi-final, Weds night",
            "emoji": "🏆",
            "category": "global",
            "scope": "global",
            "when": "Weds",
            "why": "the whole planet is watching",
            "decay_speed": "days",
            "angles": [
                {"type": "funny", "title": "Match-night snack guilt",
                 "hook": "your macros vs the World Cup", "concept_note": "relatable"}
            ],
        },
    ]
}


class FakeSearch:
    def __init__(self, payload):
        self.payload = payload

    def search(self, system, user):
        self.system = system
        self.user = user
        return json.dumps(self.payload)


def test_scout_moments_parses_and_ranks(settings):
    from chrgd.trends import scout_moments

    fake = FakeSearch(MOMENTS_PAYLOAD)
    result = scout_moments(settings, client=fake)
    assert [m.category for m in result.moments] == ["weather", "global"]
    assert result.moments[0].angles[0].type == "advice"
    assert result.moments[1].scope == "global"
    assert "COLLECTIVELY" in fake.system  # the radar brief, not the gym scout
    assert "global" in fake.system.lower()  # global moments now in scope


def test_scout_moment_detail_digs_specific_headlines(settings):
    from chrgd.trends import scout_moment_detail

    fake = FakeSearch(MOMENTS_PAYLOAD)
    result = scout_moment_detail(settings, "Wimbledon", client=fake)
    assert len(result.moments) >= 1
    # The dig brief asks for specific, current sub-stories; topic in the ask.
    assert "DEEPER" in fake.system
    assert "Wimbledon" in fake.user


def test_dig_endpoint_and_build_from_subheadline(client, settings):
    # Complete a moments scan, then dig into moment 0.
    job_id = client.post("/api/jobs/moments").json()["job_id"]
    with Store(settings.db_path) as store:
        store.update_job(job_id, status="COMPLETED", result_json=json.dumps(MOMENTS_PAYLOAD))
    r = client.post(f"/api/moments/{job_id}/dig", data={"moment": 0})
    body = r.json()
    assert body["kind"] == "moment_detail"
    assert body["topic"] == MOMENTS_PAYLOAD["moments"][0]["title"]
    with Store(settings.db_path) as store:
        dig = store.get_job(body["job_id"])
        assert dig["kind"] == "moment_detail"
        assert json.loads(dig["params_json"])["topic"] == body["topic"]
        # Simulate the dig completing with specific sub-stories.
        store.update_job(dig["job_id"], status="COMPLETED", result_json=json.dumps(MOMENTS_PAYLOAD))
    # Building from a sub-headline uses the same /use endpoint on the dig job.
    r2 = client.post(f"/api/moments/{body['job_id']}/use", data={"moment": 1, "angle": 0})
    assert r2.status_code == 200
    with Store(settings.db_path) as store:
        assert store.get_idea(r2.json()["idea_id"]) is not None
    # Bad indices + non-discovery jobs are rejected.
    assert client.post(f"/api/moments/{job_id}/dig", data={"moment": 99}).status_code == 400
    assert client.post("/api/moments/99999/dig", data={"moment": 0}).status_code == 404


def test_evergreen_scout_requires_brand_tie(settings):
    from chrgd.trends import scout_discover

    fake = FakeSearch(MOMENTS_PAYLOAD)
    scout_discover(settings, "evergreen", client=fake)
    sys = fake.system.lower()
    # Facts must sit in CHRGD's world and bridge back to the brand.
    assert "chrgd's world" in sys or "brand can naturally own" in sys
    assert "reject" in sys and "no link" in sys
    assert "bridge" in sys


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
    titles = [m["title"] for m in data["moments"]]
    assert titles[1].startswith("World Cup")
    assert data["moments"][1]["scope"] == "global"  # global moments carry scope
    assert data["age_hours"] is not None and data["age_hours"] < 1

    # One tap on an angle → seeded idea + build job.
    r = client.post(
        f"/api/moments/{job_id}/use",
        data={"moment": 1, "angle": 0, "style": "gritty", "render_mode": "ai_design"},
    )
    body = r.json()
    with Store(settings.db_path) as store:
        idea = store.get_idea(body["idea_id"])
        assert "World Cup" in idea.concept_note
        assert "your macros vs the World Cup" in idea.concept_note  # hook carried in
        assert idea.content_category == "moment"
        assert idea.decay_speed is not None and idea.decay_speed.value == "days"
        assert idea.learning_tag == "moment:global"
        route = json.loads(idea.route_json)
        assert route["style"] == "gritty" and route["render_mode"] == "ai_design"
        job = store.get_job(body["job_id"])
        assert job["kind"] == "build_one" and job["idea_id"] == idea.idea_id

    # The editor's OWN angle on a moment overrides the preset angles.
    r = client.post(
        f"/api/moments/{job_id}/use",
        data={"moment": 1, "custom": "how rare a World Cup upset like this really is — a did-you-know",
              "develop": "true"},
    )
    body = r.json()
    assert body["develop"] is True
    with Store(settings.db_path) as store:
        idea = store.get_idea(body["idea_id"])
        assert "World Cup" in idea.concept_note  # moment context kept
        assert "how rare a World Cup upset" in idea.concept_note  # own angle used
        assert idea.content_category == "moment"
        # Develop-first → a concept job, not a straight build.
        assert store.get_job(body["job_id"])["kind"] == "concept"

    # Bad indices → 400 (no custom, bad angle), unknown job → 404.
    assert client.post(f"/api/moments/{job_id}/use", data={"moment": 9, "angle": 0}).status_code == 400
    assert client.post(f"/api/moments/{job_id}/use", data={"moment": 1, "angle": 99}).status_code == 400
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


def test_manual_post_bundle(client, settings):
    with Store(settings.db_path) as store:
        store.add_idea(
            Idea(
                idea_id="G-0001",
                concept_note="x",
                slides_json=json.dumps(good_post()["slides"]),
                caption="Sweating just looking at the forecast?\nHere's how to cope.",
                hashtags=json.dumps(["#gymtok", "heatwave", "#uk"]),
                asset_paths_json=json.dumps(
                    [f"/out/G-0001/slide_{i}.jpg" for i in range(1, 4)]
                ),
            )
        )
    d = client.get("/api/ideas/G-0001/manual").json()
    assert d["count"] == 3
    # Images in slide order, renamed for a clean camera-roll.
    assert [im["filename"] for im in d["images"]] == [
        "G-0001_slide_1.jpg", "G-0001_slide_2.jpg", "G-0001_slide_3.jpg"
    ]
    assert d["images"][0]["url"] == "/media/G-0001/slide_1.jpg"
    # Caption keeps its line breaks (TikTok app allows them), then hashtags,
    # every tag prefixed with '#'.
    assert "Sweating just looking" in d["caption_text"]
    assert d["caption_text"].endswith("#gymtok #heatwave #uk")
    assert "\n\n#gymtok" in d["caption_text"]
    assert client.get("/api/ideas/NOPE/manual").status_code == 404


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


def test_brand_bible_reaches_the_engine(settings, store):
    """The persona + examples must be injected into every reasoning call."""
    from chrgd.pipeline import engine_base, load_system_prompt

    base = engine_base()
    assert "BRAND BIBLE" in base and "CHRGD is not a faceless brand" in base
    # Build, angles and concept-development all share engine_base(), and the
    # full build prompt still carries the strict JSON contract.
    sp = load_system_prompt()
    assert "BRAND BIBLE" in sp and "Output contract" in sp


# --- better posts round: moments as the star, relatability, body slides ---------


def test_moments_use_carries_moment_into_build_brief(client, settings):
    job_id = client.post("/api/jobs/moments").json()["job_id"]
    with Store(settings.db_path) as store:
        store.update_job(job_id, status="COMPLETED",
                         result_json=json.dumps(MOMENTS_PAYLOAD))
    r = client.post(f"/api/moments/{job_id}/use", data={"moment": 0, "angle": 0})
    assert r.status_code == 200
    with Store(settings.db_path) as store:
        idea = store.get_idea(r.json()["idea_id"])
    moment = json.loads(idea.route_json)["moment"]
    assert moment["title"] == "Heatwave hitting Sat-Sun, 32C"
    assert moment["why"].startswith("whole country melting")

    # The build brief inverts the usual framing: the moment is the star.
    msg = build_user_message(idea)
    assert "SHARED CULTURAL MOMENT" in msg
    assert "Heatwave hitting Sat-Sun, 32C" in msg
    assert "the brand is the sidekick" in msg.lower() or "sidekick" in msg


def test_moment_survives_build_route_overwrite(settings, store):
    route = {"moment": {"title": "World Cup semi", "why": "everyone's up at 2am"}}
    store.add_idea(Idea(idea_id="G-0001", concept_note="x",
                        route_json=json.dumps(route)))
    build_single_idea(store, settings, "G-0001", client=FakeChat([good_post()]))
    saved = json.loads(store.get_idea("G-0001").route_json)
    assert saved["moment"]["title"] == "World Cup semi"


def test_blank_canvas_build_pins_subject_to_lived_experience():
    blank = Idea(idea_id="G-1", concept_note="blank canvas")
    msg = build_user_message(blank)
    assert "SUBJECT CHOICE" in msg
    assert "obscure trivia" in msg.lower()
    # A real concept gets no such override.
    real = Idea(idea_id="G-2", concept_note="creatine timing arguments")
    assert "SUBJECT CHOICE" not in build_user_message(real)


def test_moments_scout_has_hard_awareness_bar(settings):
    from chrgd.trends import scout_moments

    fake = FakeSearch(MOMENTS_PAYLOAD)
    scout_moments(settings, client=fake)
    assert "AWARENESS TEST" in fake.system
    assert "NO quota" in fake.system
    # angles must keep the moment as the star, not a product post in a hat
    assert "the MOMENT stays the star" in fake.system


def test_engine_prompt_gains_relatability_and_density_rules():
    from chrgd.pipeline import load_system_prompt

    sp = load_system_prompt()
    assert "the moment is the STAR" in sp
    assert "Recognition gate" in sp
    assert "Vary the information density" in sp
    assert '"body"' in sp  # the JSON contract offers the detail block


def test_body_slide_flows_into_design_prompt():
    from chrgd.images import compose_design_prompt
    from chrgd.models import Slide

    brand = load_brand()
    dense = Slide(headline="The 2am problem", supporting="s",
                  body="Sentence one. Sentence two. Sentence three.",
                  image_prompt="calm editorial frame")
    prompt = compose_design_prompt(dense, brand, None)
    assert "Detail text (the readable block): Sentence one." in prompt
    assert "READER slide" in prompt
    # No body → no reader-slide instructions polluting punchy slides.
    punchy = Slide(headline="h", supporting="s", image_prompt="p")
    assert "READER slide" not in compose_design_prompt(punchy, brand, None)


def test_edit_persists_body_and_new_slides_carry_it(client, settings):
    slides = [{"headline": "h", "supporting": "s", "image_prompt": "p",
               "visual_intent": "v"}]
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x",
                            slides_json=json.dumps(slides)))
        store.mark_review("G-0001", {"hook": "h"})
    r = client.post(
        "/api/ideas/G-0001/edit",
        data={"slide_count": "2", "slide_body_0": "the full story, readable"},
    )
    assert r.json()["saved"] is True
    with Store(settings.db_path) as store:
        saved = json.loads(store.get_idea("G-0001").slides_json)
    assert saved[0]["body"] == "the full story, readable"
    assert saved[1]["body"] == ""  # new slides arrive with the field present


# --- psychology round: stage-0 mechanisms, computed per post ---------------------


def test_engine_prompt_teaches_the_psychology():
    from chrgd.pipeline import load_system_prompt

    sp = load_system_prompt()
    # The stage-0 mechanisms the engine must run every decision through.
    assert "### 0. The psychology" in sp
    assert "~200ms" in sp
    assert "Sharing is identity, not appreciation" in sp
    assert "Only high-arousal emotion moves" in sp
    assert "Peak-end" in sp
    # …and the QA stage audits them instead of taking them on trust.
    assert "psychology audit" in sp
    # The contract forces the working to be shown, not vibed.
    for field in ('"emotion"', '"hook_question"', '"share_identity"', '"named_unnamed"'):
        assert field in sp
    # Dead comment prompts are named and banned.
    assert "Never 'what do you think?'" in sp


def test_psych_block_survives_build_and_reaches_detail(client, settings):
    post = good_post()
    post["route"]["psych"] = {
        "emotion": "recognition-shock",
        "hook_question": "wait, is that why I'm knackered by 3pm?",
        "share_identity": "I understand what our sessions are really like",
        "named_unnamed": "the warm-up set you do so the guy waiting knows you're nearly done",
    }
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
        build_single_idea(store, settings, "G-0001", client=FakeChat([post]))
    d = client.get("/api/ideas/G-0001/detail").json()
    assert d["psych"]["emotion"] == "recognition-shock"
    assert d["psych"]["hook_question"].startswith("wait, is that why")
    # Old posts without a psych block degrade gracefully.
    with Store(settings.db_path) as store:
        store.add_idea(Idea(idea_id="G-0002", concept_note="y"))
        build_single_idea(store, settings, "G-0002", client=FakeChat([good_post()]))
    assert client.get("/api/ideas/G-0002/detail").json()["psych"] == {}


# --- the fan-out: competing takes before the expensive write ---------------------

TAKES_PAYLOAD = {
    "takes": [
        {"title": "The 40-min hogger exposed", "angle": "identity callout",
         "hook": "you know exactly who this is", "mechanic": "identity_exposure",
         "emotion": "recognition-shock",
         "share_identity": "I notice what really goes on in our gym",
         "sketch": "callout → the tell-tale signs → tag him",
         "concept_note": "callout carousel about rack hoggers"},
        {"title": "Rack economics", "angle": "absurd maths take",
         "hook": "40 minutes × 3 sets = a mortgage on the rack",
         "mechanic": "visual_mind_bend", "emotion": "amusement",
         "share_identity": "I'm the funny one in the chat",
         "sketch": "absurd premise → escalating maths → punchline",
         "concept_note": "surreal maths of hogging the rack"},
    ]
}


def test_generate_takes_carries_seed_and_prior(settings):
    from chrgd.pipeline import generate_takes

    idea = Idea(
        idea_id="G-0001",
        concept_note="gym bros who hog the squat rack",
        route_json=json.dumps({"moment": {"title": "Heatwave", "why": "melting"}}),
    )
    fake = FakeChat([TAKES_PAYLOAD])
    result = generate_takes(
        idea, settings, feedback="less jokey",
        prior=[{"title": "Old take", "angle": "already shown"}],
        client=fake,
    )
    assert [t.title for t in result.takes][1] == "Rack economics"
    assert result.spend_usd > 0
    system, user = fake.calls[0]
    assert "DIVERGENCE" in system            # the contract, not the build
    assert "gym bros who hog" in user        # seed context
    assert "SHARED CULTURAL MOMENT" in user  # moment travels into the fan-out
    assert "Old take" in user                # prior takes excluded from repeats
    assert "less jokey" in user              # editor steer


def test_worker_takes_job_and_refan_carries_prior(settings, store, monkeypatch):
    import chrgd.pipeline as pl
    from chrgd.pipeline import Take, TakesResult
    from chrgd.worker import Worker, enqueue_takes

    seen = []

    def fake_takes(idea, s, *, count=5, feedback="", prior=None, client=None, **kw):
        seen.append({"feedback": feedback, "prior": prior})
        return TakesResult(
            takes=[Take.model_validate(t) for t in TAKES_PAYLOAD["takes"]],
            spend_usd=0.01,
        )

    monkeypatch.setattr(pl, "generate_takes", fake_takes)
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    worker = Worker(settings)

    job1 = enqueue_takes(store, "G-0001")
    # Idempotent while the round is still pending.
    assert enqueue_takes(store, "G-0001") == job1
    worker.run_once()
    job = store.get_job(job1)
    assert job["status"] == "COMPLETED"
    assert len(json.loads(job["result_json"])["takes"]) == 2
    assert seen[0]["prior"] is None  # first round has nothing to avoid

    job2 = enqueue_takes(store, "G-0001", feedback="something warmer")
    assert job2 != job1
    worker.run_once()
    assert seen[1]["feedback"] == "something warmer"
    # The re-fan carries round 1's takes so round 2 can't repeat them.
    assert seen[1]["prior"][0]["title"] == "The 40-min hogger exposed"


def test_takes_endpoints_pick_constrains_the_build(client, settings):
    # Door opens with takes: seed + fan-out job, no build yet.
    r = client.post(
        "/api/create/start",
        data={"mode": "idea", "text": "rack hoggers", "takes": "true"},
    ).json()
    assert r["takes"] is True
    idea_id, takes_job = r["idea_id"], r["job_id"]
    with Store(settings.db_path) as store:
        job = store.get_job(takes_job)
        assert job["kind"] == "takes"
        # Simulate the worker completing the fan-out.
        store.update_job(takes_job, status="COMPLETED",
                         result_json=json.dumps(TAKES_PAYLOAD))

    # Resume support: detail exposes the latest fan-out round.
    d = client.get(f"/api/ideas/{idea_id}/detail").json()
    assert d["last_takes_job"]["job_id"] == takes_job
    assert d["last_takes_job"]["status"] == "COMPLETED"

    # Pick take 2 with a tweak → stored on the idea + build queued.
    r2 = client.post(
        f"/api/ideas/{idea_id}/takes/{takes_job}/pick",
        data={"index": "1", "tweak": "make it about leg day queues"},
    ).json()
    with Store(settings.db_path) as store:
        idea = store.get_idea(idea_id)
        assert store.get_job(r2["job_id"])["kind"] == "build_one"
    take = json.loads(idea.route_json)["take"]
    assert take["title"] == "Rack economics"
    assert take["tweak"] == "make it about leg day queues"

    # The chosen take (and the editor's tweak) constrain the full write.
    msg = build_user_message(idea)
    assert "CHOSEN TAKE" in msg
    assert "Rack economics" in msg
    assert "make it about leg day queues" in msg

    # Bad picks are rejected.
    assert client.post(
        f"/api/ideas/{idea_id}/takes/{takes_job}/pick", data={"index": "9"}
    ).status_code == 400
    assert client.post(
        f"/api/ideas/{idea_id}/takes/999999/pick", data={"index": "0"}
    ).status_code == 404


def test_take_survives_build_route_overwrite(settings, store):
    route = {"take": {"title": "Rack economics", "angle": "absurd maths"}}
    store.add_idea(Idea(idea_id="G-0001", concept_note="x",
                        route_json=json.dumps(route)))
    build_single_idea(store, settings, "G-0001", client=FakeChat([good_post()]))
    saved = json.loads(store.get_idea("G-0001").route_json)
    assert saved["take"]["title"] == "Rack economics"


def test_moments_use_can_fan_out_to_takes(client, settings):
    job_id = client.post("/api/jobs/moments").json()["job_id"]
    with Store(settings.db_path) as store:
        store.update_job(job_id, status="COMPLETED",
                         result_json=json.dumps(MOMENTS_PAYLOAD))
    r = client.post(
        f"/api/moments/{job_id}/use",
        data={"moment": "0", "angle": "0", "takes": "true"},
    ).json()
    assert r["takes"] is True
    with Store(settings.db_path) as store:
        assert store.get_job(r["job_id"])["kind"] == "takes"
        # The moment still rides the seed, so the fan-out sees it too.
        idea = store.get_idea(r["idea_id"])
    assert json.loads(idea.route_json)["moment"]["title"].startswith("Heatwave")


def test_create_journey_has_no_style_step_and_meta_covers_every_screen(client):
    """The look/style step is gone (the engine designs the branding per post),
    and every screen in the stepper MUST have a META entry — show() crashes
    on any screen that lacks one (the takes screen shipped with that bug)."""
    import re

    html = client.get("/create").text
    assert "scr-look" not in html
    assert "Pick the visual style" not in html
    assert "pickStyle" not in html
    # Length survives as an inline dial on each door (4 slots + JS selector).
    assert html.count("data-len-slot") >= 4

    screens = set(re.findall(r'id="scr-([a-z]+)"', html))
    meta_src = re.search(r"const META = \{(.*?)\};", html, re.S).group(1)
    meta_keys = set(re.findall(r"(\w+):\s*\[", meta_src))
    missing = screens - meta_keys
    assert not missing, f"screens without META entries (show() would crash): {missing}"


# --- the trending lane: participation trends, not events -------------------------

TRENDING_PAYLOAD = {
    "moments": [
        {
            "title": "'Of course' format", "emoji": "📈", "category": "format",
            "when": "wave — months of life left", "decay_speed": "weeks",
            "why": "everyone's doing the self-aware archetype joke",
            "angles": [
                {"type": "funny", "title": "Gym-bro of course",
                 "hook": "I'm a gym bro — of course I…",
                 "concept_note": "run the 'of course' format on gym archetypes"}
            ],
        },
        {
            "title": "75-Hard-alike challenge spike", "emoji": "🔥",
            "category": "challenge", "when": "spike — peaking now",
            "decay_speed": "days", "why": "gym-tok is arguing about it",
            "angles": [
                {"type": "advice", "title": "Honest review",
                 "hook": "day 40 of the challenge nobody finishes",
                 "concept_note": "honest take on the challenge wave"}
            ],
        },
    ]
}


def test_trending_scout_hunts_participation_not_events(settings):
    from chrgd.trends import scout_discover

    fake = FakeSearch(TRENDING_PAYLOAD)
    result = scout_discover(settings, "trending", client=fake)
    assert [m.category for m in result.moments] == ["format", "challenge"]
    sys = fake.system
    assert "TRENDING" in sys and "18-30" in sys
    assert "NOT news events" in sys          # events belong to the other lane
    assert "THE FEED TEST" in sys            # recognisable from the FYP, not trade press
    assert '"spike"' in sys and '"wave"' in sys  # horizon called on every trend
    assert "gym" in sys.lower() and "meme" in sys.lower()
    assert "never claim a specific sound" in sys  # the no-live-FYP limitation


def test_trending_lane_seeds_trend_framed_ideas(client, settings):
    job_id = client.post(
        "/api/jobs/moments", data={"kind": "trending"}
    ).json()["job_id"]
    with Store(settings.db_path) as store:
        assert store.get_job(job_id)["kind"] == "trending"
        store.update_job(job_id, status="COMPLETED",
                         result_json=json.dumps(TRENDING_PAYLOAD))
    # The lane feed serves it back like any discovery scan.
    feed = client.get("/api/moments?kind=trending").json()
    assert feed["moments"][0]["title"] == "'Of course' format"

    r = client.post(
        f"/api/moments/{job_id}/use", data={"moment": "0", "angle": "0"}
    ).json()
    with Store(settings.db_path) as store:
        idea = store.get_idea(r["idea_id"])
    assert idea.content_category == "trending"
    assert idea.learning_tag == "trending:format"
    route = json.loads(idea.route_json)
    assert route["moment"]["kind"] == "trending"

    # The build brief frames it as a format to follow, not an event to ride.
    msg = build_user_message(idea)
    assert "LIVE TREND" in msg
    assert "BE the trend" in msg
    assert "SHARED CULTURAL MOMENT" not in msg


def test_moment_seeds_still_use_moment_framing(client, settings):
    job_id = client.post("/api/jobs/moments").json()["job_id"]
    with Store(settings.db_path) as store:
        store.update_job(job_id, status="COMPLETED",
                         result_json=json.dumps(MOMENTS_PAYLOAD))
    r = client.post(
        f"/api/moments/{job_id}/use", data={"moment": "0", "angle": "0"}
    ).json()
    with Store(settings.db_path) as store:
        idea = store.get_idea(r["idea_id"])
    assert idea.content_category == "moment"
    msg = build_user_message(idea)
    assert "SHARED CULTURAL MOMENT" in msg and "LIVE TREND" not in msg


# --- evidence-anchored concepts: playbook + precedent + account history ----------


def test_playbook_reaches_every_reasoning_call():
    from chrgd.pipeline import engine_base, load_playbook

    pb = load_playbook()
    assert "archetype taxonomy" in pb          # the library shipped
    base = engine_base()
    assert "VIRAL PLAYBOOK" in base            # ...and rides the shared base,
    assert "The unsaid thing, said" in base    # so build/takes/angles/concept
    assert "anchor concepts in these precedents" in base.lower() or \
           "anchor concepts in" in base


def test_takes_demand_precedent_and_account_history(settings):
    from chrgd.pipeline import generate_takes

    payload = {"takes": [dict(TAKES_PAYLOAD["takes"][0],
                              precedent="archetype taxonomy — self-categorisation engine")]}
    fake = FakeChat([payload])
    idea = Idea(idea_id="G-0001", concept_note="rack hoggers")
    result = generate_takes(
        idea, settings, client=fake,
        performance_notes="WHAT WORKS FOR THIS ACCOUNT: moment posts hit",
    )
    assert result.takes[0].precedent.startswith("archetype taxonomy")
    system, user = fake.calls[0]
    # The contract demands evidence per take and caps unproven guesses at one.
    assert "EVIDENCE FIRST" in system
    assert '"precedent"' in system
    assert "no precedent — experimental" in system
    # The account's own hit/flop history steers the fan-out.
    assert "WHAT WORKS FOR THIS ACCOUNT: moment posts hit" in user


def test_worker_takes_passes_performance_notes(settings, store, monkeypatch):
    import chrgd.pipeline as pl
    import chrgd.worker as wk
    from chrgd.pipeline import TakesResult
    from chrgd.worker import Worker, enqueue_takes

    seen = {}

    def fake_takes(idea, s, **kw):
        seen.update(kw)
        return TakesResult()

    monkeypatch.setattr(pl, "generate_takes", fake_takes)
    monkeypatch.setattr(
        "chrgd.learning.performance_notes", lambda st: "NOTES SENTINEL"
    )
    store.add_idea(Idea(idea_id="G-0001", concept_note="x"))
    enqueue_takes(store, "G-0001")
    Worker(settings).run_once()
    assert seen["performance_notes"] == "NOTES SENTINEL"


def test_chosen_take_precedent_reaches_build_brief():
    idea = Idea(
        idea_id="G-0001", concept_note="x",
        route_json=json.dumps({"take": {
            "title": "Rack economics",
            "precedent": "absurd commitment bit — committed absurdity travels",
        }}),
    )
    msg = build_user_message(idea)
    assert "precedent: absurd commitment bit" in msg
