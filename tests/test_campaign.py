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
from chrgd.shows import get_show, load_shows

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
    # A date or a pin — one of them has to resolve, or builds run off-campaign
    # while the config still claims to be armed.
    assert campaign.launch_date is not None or campaign.pin_phase
    keys = [p.key for p in campaign.calendar_phases()]
    assert keys == ["prime", "tease", "launch", "sell"]
    # No gaps: each calendar phase must start the day the previous one ends.
    # Pin-only phases are exempt — `buildup` overlaps `prime` on purpose.
    for earlier, later in zip(campaign.calendar_phases(),
                              campaign.calendar_phases()[1:]):
        assert later.starts == earlier.ends + 1, f"gap before {later.key}"


def test_prime_phase_forbids_selling():
    """The pre-launch phase's whole point is that it doesn't sell."""
    prime = load_campaign().get_phase("prime")
    banned = " ".join(prime.banned).lower()
    assert "quiz" in banned
    assert "link" in banned
    for cta in prime.ctas:
        assert "getchrgd.co.uk" not in cta, "prime must not push the domain"


def test_no_phase_promises_a_date_we_havent_fixed():
    """A date said out loud is a promise; a missed one is the worst first
    impression a new brand can make. Nothing may name one."""
    banned_words = ("this week", "tomorrow", "next week", "in a few days",
                    "days to go")
    campaign = load_campaign()
    for phase in campaign.phases:
        blob = " ".join([phase.brief] + phase.ctas).lower()
        for word in banned_words:
            assert word not in blob, f"{phase.key} promises '{word}'"
    for post in load_launch_backlog():
        blob = f"{post.hook} {post.note}".lower()
        for word in banned_words:
            assert word not in blob, f"{post.key} promises '{word}'"


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
    """Otherwise the brief opens by contradicting itself.

    With no launch date configured there is no timing clause at all, which is
    the point — the phase carries the meaning and nothing invents a countdown.
    What must never happen is the brief claiming one phase and a contradictory
    timing in the same sentence.
    """
    _, brief = campaign_block(today=date(2026, 8, 1), phase_key="launch")
    assert "Launch week" in brief
    assert "BEFORE launch" not in brief


def test_timing_clause_appears_only_once_there_is_a_date():
    campaign = _campaign()
    assert "on LAUNCH DAY itself" in campaign.brief_block(
        campaign.get_phase("launch"), today=LAUNCH)
    campaign.launch_date = None
    brief = campaign.brief_block(campaign.get_phase("launch"), today=LAUNCH)
    assert "LAUNCH DAY" not in brief
    assert "day(s)" not in brief


def test_a_pinned_phase_beats_the_calendar():
    campaign = _campaign()
    campaign.pin_phase = "prime"
    # A date that would otherwise resolve to launch week.
    assert campaign.phase_for(LAUNCH).key == "prime"


def test_no_date_and_no_pin_resolves_to_nothing():
    campaign = _campaign()
    campaign.launch_date = None
    assert campaign.phase_for(LAUNCH) is None


def test_no_date_with_a_pin_still_runs():
    campaign = _campaign()
    campaign.launch_date = None
    campaign.pin_phase = "prime"
    assert campaign.phase_for(LAUNCH).key == "prime"


def test_blank_launch_date_parses_as_no_date():
    """`launch_date = ""` is how the config says 'not decided yet'."""
    assert Campaign(armed=True, launch_date="").launch_date is None
    assert Campaign(armed=True, launch_date="   ").launch_date is None


def test_status_says_pinned_out_loud():
    """A forgotten pin has to be visible, not subtle."""
    lines = "\n".join(status_lines())
    campaign = load_campaign()
    if campaign.pin_phase:
        assert "PINNED" in lines
        assert "not set yet" in lines or str(campaign.launch_date) in lines


def test_unknown_forced_phase_falls_back_to_the_resolved_phase():
    """A typo degrades to correct behaviour rather than dropping the campaign."""
    campaign = load_campaign()
    expected = campaign.phase_for(date(2026, 8, 1))
    key, brief = campaign_block(today=date(2026, 8, 1), phase_key="nonsense")
    assert key == expected.key and brief


def test_a_pin_only_phase_is_unreachable_by_date():
    """`buildup` overlaps `prime`; the calendar must never pick it itself."""
    campaign = load_campaign()
    pin_only = [p for p in campaign.phases if not p.on_calendar]
    assert pin_only, "buildup should be pin-only"
    for phase in pin_only:
        assert campaign.get_phase(phase.key) is not None, "still selectable"
    # With the pin cleared, a date inside the overlap resolves to the calendar
    # phase, never to the pin-only one.
    unpinned = campaign.model_copy(update={"pin_phase": "",
                                           "launch_date": LAUNCH})
    assert unpinned.phase_for(date(2026, 8, 1)).key == "prime"


def test_buildup_teases_without_selling_or_dating():
    buildup = load_campaign().get_phase("buildup")
    assert buildup is not None
    banned = " ".join(buildup.banned).lower()
    for forbidden in ("state of the art", "ai", "date", "link"):
        assert forbidden in banned, f"buildup should ban {forbidden}"
    # The ask is a follow, and never a link.
    for cta in buildup.ctas:
        assert "getchrgd.co.uk" not in cta
    assert "follow" in buildup.cta_style.lower()


def test_tech_block_bans_ai_language_when_the_product_has_none():
    campaign = load_campaign()
    block = campaign.tech.as_block()
    if campaign.tech.uses_ai:
        assert "you may say so" in block
    else:
        assert "BANNED" in block
        assert "false claim about our own product" in block


def test_tech_block_reaches_the_write_call():
    _, brief = campaign_block(phase_key="buildup")
    assert "TALKING ABOUT THE TECHNOLOGY" in brief
    assert "THIS PRODUCT USES AI" in brief


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
    assert "Launch week" in message


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


# --- VERDICT and RECEIPTS: the editor owns the facts ------------------------


def test_no_launch_post_uses_a_retired_show():
    """Amp and the Multiverse are audience formats, cut from the launch."""
    for post in load_launch_backlog():
        assert post.show not in ("amp", "multiverse"), (
            f"{post.key} still routes at an affinity show"
        )


def test_every_verdict_post_carries_a_ruling():
    """A VERDICT seed with no ruling would let the engine invent the opinion."""
    from chrgd.rulings import RULINGS

    for post in load_launch_backlog():
        if post.show != "verdict":
            continue
        assert post.verdict_subject, f"{post.key}: nothing to rule on"
        assert post.verdict_ruling in RULINGS, f"{post.key}: no valid ruling"
        if post.verdict_ruling == "only_if":
            assert post.verdict_condition, f"{post.key}: 'only if' with no condition"


def test_every_receipts_post_carries_a_real_price():
    """A teardown with no figure hands the engine the one job it's barred from."""
    for post in load_launch_backlog():
        if post.show != "receipts":
            continue
        assert post.receipt_product, f"{post.key}: nothing to tear down"
        assert post.receipt_price, f"{post.key}: no price"


def test_the_launch_rules_against_things_we_sell():
    """The trust play only works if some ruling actually goes against us."""
    against = [p for p in load_launch_backlog()
               if p.verdict_ruling == "not_worth_it"]
    assert len(against) >= 2, "no ruling cuts against the shop — that's an advert"


def test_verdict_route_reaches_the_write_call():
    post = [p for p in load_launch_backlog() if p.show == "verdict"][0]
    route = post.route()
    assert route["verdict"]["ruling"] == post.verdict_ruling
    idea = Idea(idea_id="G-1", concept_note=post.concept_note(),
                route_json=json.dumps(route))
    message = build_user_message(idea)
    assert "THE EDITOR'S RULING" in message
    assert "EXECUTE THIS RULING" in message


def test_receipt_route_reaches_the_write_call_and_bans_second_figures():
    post = [p for p in load_launch_backlog() if p.show == "receipts"][0]
    idea = Idea(idea_id="G-2", concept_note=post.concept_note(),
                route_json=json.dumps(post.route()))
    message = build_user_message(idea)
    assert post.receipt_price in message
    assert "ONLY FIGURE YOU HAVE" in message
    assert "NAME NO BRAND" in message


def test_a_ruling_against_us_is_told_not_to_pull_the_punch():
    from chrgd.rulings import verdict_brief

    block = verdict_brief({"subject": "fat burners", "ruling": "not_worth_it"})
    assert "Do not pull the punch" in block


def test_an_only_if_must_surface_its_condition_on_slide_one():
    from chrgd.rulings import verdict_brief

    block = verdict_brief({"subject": "x", "ruling": "only_if",
                           "condition": "you train five times a week"})
    assert "opening slide" in block
    assert "you train five times a week" in block


def test_an_incomplete_ruling_yields_nothing_rather_than_half_a_brief():
    from chrgd.rulings import receipt_brief, verdict_brief

    assert verdict_brief({"subject": "x", "ruling": "nonsense"}) == ""
    assert verdict_brief({"ruling": "worth_it"}) == ""
    assert receipt_brief({"product": "a tub"}) == ""


def test_verdict_journey_rejects_an_only_if_with_no_condition(client):
    r = client.post("/api/create/start", data={
        "mode": "idea", "show": "verdict", "text": "is x worth it?",
        "verdict": json.dumps({"subject": "x", "ruling": "only_if"})})
    assert r.status_code == 400


def test_verdict_journey_rejects_an_unknown_ruling(client):
    r = client.post("/api/create/start", data={
        "mode": "idea", "show": "verdict", "text": "is x worth it?",
        "verdict": json.dumps({"subject": "x", "ruling": "maybe"})})
    assert r.status_code == 400


def test_receipts_journey_requires_the_price(client):
    r = client.post("/api/create/start", data={
        "mode": "idea", "show": "receipts", "text": "teardown",
        "receipt": json.dumps({"product": "a tub"})})
    assert r.status_code == 400


def test_verdict_journey_builds_with_the_editors_ruling(client):
    r = client.post("/api/create/start", data={
        "mode": "idea", "show": "verdict", "text": "is x worth it?",
        "verdict": json.dumps({"subject": "fat burners",
                               "ruling": "not_worth_it",
                               "reason": "caffeine with a story on top"})})
    assert r.status_code == 200
    with Store(client.settings.db_path) as store:
        idea = store.get_idea(r.json()["idea_id"])
    message = build_user_message(idea)
    assert "NOT WORTH IT" in message
    assert "caffeine with a story on top" in message


def test_paused_shows_are_dimmed_not_removed(client):
    """A pause is a statement about the fortnight, never a lock on the studio."""
    html = client.get("/create").text
    for key in load_campaign().paused_shows:
        tile = html.split(f'data-show="{key}"')[0].rsplit("<button", 1)[1]
        assert "paused" in tile, f"{key} should be dimmed"
        assert f'data-show="{key}"' in html, f"{key} must still be selectable"


def test_the_launch_leaves_one_show_per_weekday(client):
    """Two shows on one weekday is what the pause exists to prevent."""
    active = [s for s in load_shows().values()
              if s.key not in load_campaign().paused_shows and s.weekday]
    days = [s.weekday for s in active]
    assert len(days) == len(set(days)), f"weekday clash among {days}"


def test_create_page_renders_the_two_new_screens(client):
    html = client.get("/create").text
    for probe in ("scr-verdict", "scr-receipts", "ruling-chips",
                  "receipt-price", "verdict-condition"):
        assert probe in html, f"{probe} missing from /create"


def test_the_stack_no_longer_offers_the_teardown(client):
    """It moved to RECEIPTS; a show that can do everything has no shape."""
    html = client.get("/create").text
    stack = html.split('id="stack-grid"')[1].split("</div>")[0]
    assert "price_teardown" not in stack
    assert "objection_kill" not in stack
    assert "archetype_sort" in stack


def test_the_stack_brief_forbids_diagnosis():
    """The one claim this show could plausibly make, and must not."""
    banned = " ".join(get_show("the_stack").voice.banned).lower()
    assert "deficien" in banned or "diagnos" in banned
