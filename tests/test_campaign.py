"""The campaign layer.

The property that matters most here is the same one the show layer protects:
**a disarmed campaign is a true pass-through.** A launch is a temporary state,
and the studio has to come out the other side of it building exactly what it
built before. If the null case goes red, every off-campaign post has silently
changed behaviour.

The rest covers the bit that is genuinely easy to get wrong: phase resolution
off a date, where an off-by-one puts launch-day copy on the day before launch.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from chrgd.campaign import (
    DISARMED,
    Campaign,
    LaunchPost,
    Phase,
    campaign_block,
    load_campaign,
    load_launch_backlog,
    planned_posts,
    seed_posts,
    status_lines,
)
from chrgd.config import Settings
from chrgd.db import Store
from chrgd.mechanics import get_mechanic
from chrgd.models import Idea
from chrgd.pipeline import build_user_message, creation_prefs
from chrgd.shows import get_show

LAUNCH = date(2026, 8, 31)


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        CHRGD_DB_PATH=tmp_path / "chrgd.db",
        CHRGD_OUTPUT_DIR=tmp_path / "out",
    )


@pytest.fixture()
def store(settings):
    with Store(settings.db_path) as store:
        yield store


# --- the null case ----------------------------------------------------------


def test_disarmed_campaign_injects_nothing():
    assert DISARMED.phase_for(LAUNCH) is None
    assert DISARMED.brief_block is not None  # exists, but is never reached


def test_disarmed_build_message_is_unchanged(monkeypatch):
    """A build with the campaign off must be byte-identical to a pre-campaign one."""
    idea = Idea(idea_id="G-0001", concept_note="gym bros who hog the squat rack")

    monkeypatch.setattr("chrgd.campaign.load_campaign", lambda *a, **k: DISARMED)
    monkeypatch.setattr("chrgd.campaign.campaign_block", lambda *a, **k: ("", ""))
    without = build_user_message(idea)

    assert "THE CAMPAIGN" not in without


def test_missing_config_runs_disarmed(tmp_path):
    assert load_campaign(tmp_path / "nope.toml") is DISARMED


def test_malformed_config_runs_disarmed_rather_than_raising(tmp_path):
    bad = tmp_path / "campaign.toml"
    bad.write_text("this is not [valid toml", encoding="utf-8")
    assert load_campaign(bad) is DISARMED


# --- phase resolution -------------------------------------------------------


def _campaign() -> Campaign:
    return Campaign(
        armed=True,
        key="test",
        launch_date=LAUNCH,
        phases=[
            Phase(key="prime", starts=-60, ends=-4, cta_style="comment"),
            Phase(key="tease", starts=-3, ends=-1),
            Phase(key="launch", starts=0, ends=7),
            Phase(key="sell", starts=8, ends=3650),
        ],
    )


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 8, 1), "prime"),
        (date(2026, 8, 27), "prime"),   # -4, the last prime day
        (date(2026, 8, 28), "tease"),   # -3, the first tease day
        (date(2026, 8, 30), "tease"),   # -1, the eve
        (date(2026, 8, 31), "launch"),  # launch day itself
        (date(2026, 9, 7), "launch"),   # +7, the last launch day
        (date(2026, 9, 8), "sell"),     # +8
        (date(2027, 1, 1), "sell"),
    ],
)
def test_phase_boundaries(day, expected):
    phase = _campaign().phase_for(day)
    assert phase is not None and phase.key == expected


def test_before_every_window_resolves_to_no_phase():
    assert _campaign().phase_for(date(2025, 1, 1)) is None


def test_unarmed_campaign_has_no_phase():
    campaign = _campaign()
    campaign.armed = False
    assert campaign.phase_for(LAUNCH) is None


def test_no_launch_date_has_no_phase():
    campaign = _campaign()
    campaign.launch_date = None
    assert campaign.phase_for(LAUNCH) is None


# --- the brief --------------------------------------------------------------


def test_brief_names_the_phase_and_the_ask():
    campaign = _campaign()
    phase = campaign.phase_for(date(2026, 8, 1))
    block = campaign.brief_block(phase, today=date(2026, 8, 1))
    assert "THE CAMPAIGN" in block
    assert "30 day(s) BEFORE launch" in block
    assert "comment" in block


def test_brief_calls_launch_day_by_name():
    campaign = _campaign()
    phase = campaign.phase_for(LAUNCH)
    assert "LAUNCH DAY" in campaign.brief_block(phase, today=LAUNCH)


def test_banned_moves_reach_the_write_call():
    campaign = _campaign()
    campaign.phases[0].banned = ["any mention of the quiz"]
    phase = campaign.phase_for(date(2026, 8, 1))
    assert "any mention of the quiz" in campaign.brief_block(phase)


def test_never_say_list_reaches_the_write_call():
    campaign = _campaign()
    campaign.facts.never_say = ["dropship"]
    phase = campaign.phase_for(LAUNCH)
    assert "NEVER SAY" in campaign.brief_block(phase)


# --- the shipped config -----------------------------------------------------


def test_shipped_campaign_loads_and_is_coherent():
    campaign = load_campaign()
    assert campaign.armed, "the launch campaign should ship armed"
    assert campaign.launch_date is not None
    keys = [p.key for p in campaign.phases]
    assert keys == ["prime", "tease", "launch", "sell"]
    # No gaps: each phase must start the day the previous one ends.
    for earlier, later in zip(campaign.phases, campaign.phases[1:]):
        assert later.starts == earlier.ends + 1, f"gap before {later.key}"


def test_prime_phase_forbids_selling():
    """The pre-launch phase's whole point is that it doesn't sell."""
    prime = load_campaign().get_phase("prime")
    banned = " ".join(prime.banned).lower()
    assert "quiz" in banned
    assert "link" in banned
    for cta in prime.ctas:
        assert "getchrgd.co.uk" not in cta, "prime must not push the domain"


def test_launch_phase_names_the_domain():
    launch = load_campaign().get_phase("launch")
    assert any("getchrgd.co.uk" in cta for cta in launch.ctas)


def test_status_lines_report_the_phase():
    lines = "\n".join(status_lines(date(2026, 8, 31)))
    assert "Launch" in lines


# --- the written backlog ----------------------------------------------------


def test_launch_backlog_loads_in_day_order():
    posts = load_launch_backlog()
    assert posts, "the launch backlog should ship with content in it"
    days = [p.day for p in posts]
    assert days == sorted(days)


def test_every_planned_post_routes_to_a_real_show_and_mechanic():
    for post in load_launch_backlog():
        if post.show:
            assert get_show(post.show) is not None, f"{post.key}: unknown show"
        if post.mechanic:
            assert get_mechanic(post.mechanic) is not None, (
                f"{post.key}: unknown mechanic"
            )


def test_every_planned_post_names_a_real_phase():
    phases = {p.key for p in load_campaign().phases}
    for post in load_launch_backlog():
        assert post.phase in phases, f"{post.key}: phase '{post.phase}' doesn't exist"


def test_planned_post_keys_are_unique():
    keys = [p.key for p in load_launch_backlog()]
    assert len(keys) == len(set(keys))


def test_prime_posts_never_mention_the_domain():
    """The pre-launch content must be sellable-free in the seed, not just in the brief."""
    for post in planned_posts("prime"):
        blob = f"{post.hook} {post.note}".lower()
        assert "getchrgd.co.uk" not in blob, f"{post.key} pushes the domain in prime"


def test_launch_posts_ask_for_the_quiz():
    """Every launch post carries an ask.

    Not every one spells the domain out — the phase brief supplies that, and a
    couple of seeds deliberately say "soft quiz close" instead, because on
    STRAIGHT UP the trust is worth more than the click. What must hold is that
    no launch-week seed is silent about the ask.
    """
    posts = planned_posts("launch")
    assert posts
    for post in posts:
        blob = f"{post.hook} {post.note}".lower()
        assert "quiz" in blob or "getchrgd.co.uk" in blob, (
            f"{post.key}: a launch-week post with no ask in the seed"
        )
    named = sum("getchrgd.co.uk" in p.note.lower() for p in posts)
    assert named >= len(posts) // 2, "most launch posts should name the domain"


def test_mechanic_lock_carries_the_resolved_skeleton():
    post = LaunchPost(key="x", mechanic="archetype_sort", note="n")
    lock = post.route()["mechanic_lock"]
    assert lock["name"]
    assert lock["skeleton"], "the write call reads the skeleton off route_json"


def test_unknown_mechanic_drops_the_lock_rather_than_stamping_a_dud():
    post = LaunchPost(key="x", mechanic="not_a_mechanic", note="n")
    assert "mechanic_lock" not in post.route()


# --- seeding ----------------------------------------------------------------


def test_seeding_stamps_show_mechanic_and_phase(store, settings):
    posts = [
        LaunchPost(
            key="t1",
            phase="prime",
            show="the_stack",
            mechanic="archetype_sort",
            hook="four types of supplement buyer",
            note="Sort the audience into four types.",
        )
    ]
    created, skipped = seed_posts(store, settings, posts)
    assert len(created) == 1 and not skipped

    prefs = creation_prefs(created[0])
    assert prefs["show"] == "the_stack"
    assert prefs["mechanic_lock"]["key"] == "archetype_sort"
    assert prefs["campaign_phase"] == "prime"
    assert "four types of supplement buyer" in created[0].concept_note


def test_seeding_is_idempotent(store, settings):
    posts = [LaunchPost(key="t1", phase="prime", note="A one-off thought.")]
    first, _ = seed_posts(store, settings, posts)
    second, skipped = seed_posts(store, settings, posts)
    assert len(first) == 1
    assert not second and skipped == ["t1"]


def test_seeded_row_carries_its_phase_into_the_write_call(store, settings):
    posts = [
        LaunchPost(key="t1", phase="launch", show="the_stack", note="A launch post.")
    ]
    created, _ = seed_posts(store, settings, posts)
    message = build_user_message(created[0])
    assert "THE CAMPAIGN" in message
    assert "getchrgd.co.uk" in message


def test_forced_phase_beats_the_calendar():
    """Batching next week's launch posts today must use next week's brief."""
    _, brief = campaign_block(today=date(2026, 8, 1), phase_key="launch")
    assert "getchrgd.co.uk" in brief


def test_forced_phase_is_dated_from_the_phase_not_from_today():
    """Otherwise the brief opens by contradicting itself."""
    _, brief = campaign_block(today=date(2026, 8, 1), phase_key="launch")
    assert "LAUNCH DAY" in brief
    assert "BEFORE launch" not in brief


def test_unknown_forced_phase_falls_back_to_the_calendar():
    key, brief = campaign_block(today=date(2026, 8, 1), phase_key="nonsense")
    assert key == "prime" and brief


# --- the show the campaign runs on ------------------------------------------


def test_the_stack_show_is_registered_and_sorted_into_the_week():
    show = get_show("the_stack")
    assert show is not None
    assert show.weekday == "sat", "the weekdays mon-fri are taken by the other shows"
    assert show.gate_profile == "diagnostic_pull"


# --- the create journeys ----------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A logged-in web client on a scratch database."""
    from fastapi.testclient import TestClient

    from chrgd.webapp import create_app

    monkeypatch.setenv("CHRGD_DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("CHRGD_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("CHRGD_WEB_PASSWORD", "testpw123")
    monkeypatch.setenv("CHRGD_SECRET_KEY", "0123456789abcdef0123456789abcdef")
    settings = Settings()
    client = TestClient(create_app(settings))
    client.post(
        "/login",
        data={"username": "admin", "password": "testpw123"},
        follow_redirects=False,
    )
    client.settings = settings
    return client


def test_create_page_renders_both_new_journeys(client):
    html = client.get("/create").text
    for probe in ("scr-launch", "scr-thestack", "launch-door",
                  "phase-chips", "plan-grid", "stack-grid"):
        assert probe in html, f"{probe} missing from /create"


def test_create_page_offers_every_planned_post(client):
    html = client.get("/create").text
    for post in load_launch_backlog():
        assert f'data-plan="{post.key}"' in html, f"{post.key} not offered"


def test_every_planned_post_has_a_human_readable_title():
    """The topical posts carry no hook on purpose — they must not show a raw key."""
    for post in load_launch_backlog():
        title = post.title()
        assert title and title != post.key
        assert "_" not in title or " " in title, f"{post.key}: raw key on a card"


def test_launch_journey_stamps_the_phase(client):
    r = client.post("/api/create/start", data={
        "mode": "idea", "campaign_phase": "launch", "show": "the_stack",
        "mechanic": "demo_post", "text": "answer three questions"})
    assert r.status_code == 200
    with Store(client.settings.db_path) as store:
        idea = store.get_idea(r.json()["idea_id"])
    prefs = creation_prefs(idea)
    assert prefs["campaign_phase"] == "launch"
    assert prefs["show"] == "the_stack"
    assert prefs["mechanic_lock"]["key"] == "demo_post"


def test_launch_journey_batches_ahead_of_the_calendar(client):
    """Writing launch week during prime must use the launch brief."""
    r = client.post("/api/create/start", data={
        "mode": "idea", "campaign_phase": "launch", "text": "a launch post"})
    with Store(client.settings.db_path) as store:
        idea = store.get_idea(r.json()["idea_id"])
    message = build_user_message(idea)
    assert "getchrgd.co.uk" in message
    assert "LAUNCH DAY" in message


def test_unknown_phase_is_rejected_not_silently_dropped(client):
    r = client.post("/api/create/start", data={
        "mode": "idea", "campaign_phase": "nonsense", "text": "x"})
    assert r.status_code == 400


def test_stack_journey_builds_as_the_show(client):
    r = client.post("/api/create/start", data={
        "mode": "idea", "show": "the_stack", "mechanic": "cupboard_audit",
        "text": "the cupboard of someone six months in"})
    assert r.status_code == 200
    with Store(client.settings.db_path) as store:
        idea = store.get_idea(r.json()["idea_id"])
    message = build_user_message(idea)
    assert "THE STACK" in message
    assert "THE SORT" in message, "the show's spine should reach the write call"


def test_started_posts_are_marked_on_the_launch_screen(client):
    """A planned post already seeded shows as started rather than offered twice."""
    post = planned_posts("prime")[0]
    with Store(client.settings.db_path) as store:
        seed_posts(store, client.settings, [post])
    html = client.get("/create").text
    marked = html.split(f'data-plan="{post.key}"')[1].split("</button>")[0]
    assert "started" in marked


def test_the_stack_brief_forbids_diagnosis():
    """The one claim this show could plausibly make, and must not."""
    banned = " ".join(get_show("the_stack").voice.banned).lower()
    assert "deficien" in banned or "diagnos" in banned
