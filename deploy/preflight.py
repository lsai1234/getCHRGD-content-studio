"""Pre-flight: is this box actually ready to serve?

Run on the VPS from the app directory, BEFORE starting the service:

    .venv/bin/python deploy/preflight.py

Complements `diagnose.py`, which tests connectivity to OpenAI and costs about a
penny. This one is **offline and free**: no API key needed, no paid calls, no
network. It checks the things that let a deploy boot happily and then fail the
first time someone clicks Create — a typo in a show's TOML, a style preset that
doesn't exist, a font path that isn't on this box, a secret left at its example
value.

Exit code 0 = ready, 1 = something needs fixing. Safe to run in CI.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILURES: list[str] = []
WARNINGS: list[str] = []


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"  ✗ {msg}")


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"  ! {msg}")


def section(title: str) -> None:
    print(f"\n{title}")


# --- the content configs ----------------------------------------------------


def check_shows() -> None:
    section("Shows")
    from chrgd.brand import load_brand
    from chrgd.learning import METRIC_FIELDS
    from chrgd.shows import load_gate_profiles, load_shows

    shows = load_shows()
    if not shows:
        fail("no shows loaded — config/shows/*.toml missing or unreadable")
        return
    ok(f"{len(shows)} shows: {', '.join(sorted(shows))}")

    brand = load_brand()
    profiles = load_gate_profiles()
    for show in shows.values():
        where = f"config/shows/{show.key}.toml"
        if not show.spine.roles:
            fail(f"{where}: no spine — it would fall back to the generic engine")
        if len(show.spine.briefs) != len(show.spine.roles):
            fail(f"{where}: {len(show.spine.briefs)} briefs for "
                 f"{len(show.spine.roles)} spine roles")
        if not show.voice.block.strip():
            fail(f"{where}: no voice block")
        preset = show.look.style_preset
        if preset and not brand.style_prompt(preset):
            fail(f"{where}: style_preset '{preset}' is not in brand.toml — "
                 "the show would silently lose its art direction")
        if show.gate_profile and show.gate_profile not in profiles:
            fail(f"{where}: gate_profile '{show.gate_profile}' is not in "
                 "config/gate_profiles.toml")
        if show.kpi_metric and show.kpi_metric not in METRIC_FIELDS:
            fail(f"{where}: kpi_metric '{show.kpi_metric}' is not a logged metric")
        if show.slides_min > show.slides_max:
            fail(f"{where}: slides_min > slides_max")
    if not FAILURES:
        ok("every show has a spine, a voice, a real style preset and a valid gate")


def check_roster() -> None:
    section("The Multiverse roster")
    from chrgd.roster import PROTECTED, load_roster, load_world

    roster = load_roster()
    if not roster:
        fail("no roster — config/roster.toml missing; the Multiverse can't cast")
        return
    protected = [c for c in roster.values() if c.protected]
    ok(f"{len(roster)} characters ({len(protected)} under the likeness gate)")

    for char in roster.values():
        where = f"config/roster.toml [{char.key}]"
        if char.cls not in ("meme_character", *PROTECTED):
            fail(f"{where}: unknown class '{char.cls}' — safety rules wouldn't apply")
        for field_name in ("name", "trait", "visual"):
            if not getattr(char, field_name, "").strip():
                fail(f"{where}: no {field_name}")
        if char.protected and "caricature" not in char.visual_lock().lower():
            fail(f"{where}: a protected character whose visual lock doesn't "
                 "force caricature — this is a likeness rule, not a preference")
        if not char.bits:
            warn(f"{where}: no signature bits — the engine will invent a "
                 "personality for them every episode")
    world = load_world()
    if world.max_cast < 2:
        fail("config/roster.toml [world]: max_cast under 2")
    ok(f"world set: {world.location[:48]}… · max cast {world.max_cast}")


def check_content_libraries() -> None:
    section("Content libraries")
    from chrgd.character import load_situations
    from chrgd.ingredients import load_ingredients
    from chrgd.sessions import load_axes
    from chrgd.territories import in_season, load_territories

    checks = [
        ("Amp situations", load_situations(), "config/amp_situations.toml"),
        ("Straight Up ingredients", load_ingredients(), "config/ingredients.toml"),
        ("Session axes", load_axes(), "config/session_variants.toml"),
        ("Live Wire territories", load_territories(), "config/territories.toml"),
    ]
    for label, loaded, path in checks:
        if not loaded:
            fail(f"{label}: nothing loaded from {path} — that show's screen "
                 "would open empty")
        else:
            ok(f"{label}: {len(loaded)} entries")

    if load_axes():
        required = [a.label for a in load_axes().values() if a.required]
        if not required:
            warn("config/session_variants.toml: no required axes")
    if load_territories() and not in_season():
        fail("config/territories.toml: nothing is in season today — Live Wire "
             "would scan nothing")


def check_gate_profiles() -> None:
    section("Quality gates")
    from chrgd.claims import PATTERNS
    from chrgd.shows import load_gate_profiles

    profiles = load_gate_profiles()
    if not profiles:
        warn("no gate profiles — every show would be judged on the hot-take "
             "rubric, which is what the show layer exists to stop")
    else:
        ok(f"{len(profiles)} gate profiles: {', '.join(sorted(profiles))}")
    for key, profile in profiles.items():
        if not profile.judge_focus.strip():
            fail(f"config/gate_profiles.toml [{key}]: no judge_focus — it would "
                 "silently behave as the default profile")
    ok(f"claims lint: {len(PATTERNS)} patterns armed")


def check_campaign() -> None:
    """The launch campaign, and the content queued behind it.

    Same reasoning as the show checks: `config/campaign.toml` and
    `config/launch_backlog.toml` are config files that a build reads at write
    time, so a typo in either is a *deploy-time* problem that would otherwise
    surface as a wrong-phase post going out on launch day. The routing checks
    here are the campaign's version of "style_preset that isn't in brand.toml".
    """
    section("Launch campaign")
    from datetime import date

    from chrgd.campaign import load_campaign, load_launch_backlog
    from chrgd.mechanics import get_mechanic
    from chrgd.ingredients import get_ingredient
    from chrgd.shows import get_show

    campaign = load_campaign()
    if not campaign.armed:
        ok("campaign disarmed — builds run off-campaign (a valid steady state)")
        return

    # No date is a legitimate state — you pin a phase instead. No date AND no
    # pin is the broken one: nothing resolves and every build runs off-campaign
    # while the config still claims to be armed.
    if campaign.launch_date is None and not campaign.pin_phase:
        fail("config/campaign.toml: armed with no launch_date and no "
             "pin_phase — nothing resolves, so every build would silently run "
             "off-campaign")
        return
    if campaign.pin_phase:
        if campaign.get_phase(campaign.pin_phase) is None:
            fail(f"config/campaign.toml: pin_phase '{campaign.pin_phase}' is "
                 "not one of the phases below it")
            return
        ok(f"{campaign.label or campaign.key} · PINNED to "
           f"'{campaign.pin_phase}'"
           + (f" (launch {campaign.launch_date} set but overridden)"
              if campaign.launch_date else " · no launch date yet"))
    else:
        ok(f"{campaign.label or campaign.key} · launch {campaign.launch_date}")

    if not campaign.phases:
        fail("config/campaign.toml: armed with no phases")
        return

    # A gap between phases is a day the campaign silently stops running.
    for earlier, later in zip(campaign.phases, campaign.phases[1:]):
        if later.starts != earlier.ends + 1:
            fail(f"config/campaign.toml: gap or overlap between phase "
                 f"'{earlier.key}' (ends {earlier.ends:+d}) and '{later.key}' "
                 f"(starts {later.starts:+d}) — those days would build "
                 "off-campaign")
    for phase in campaign.phases:
        where = f"config/campaign.toml [{phase.key}]"
        if not phase.brief.strip():
            fail(f"{where}: no brief — the phase would inject a bare heading")
        if not phase.ctas:
            fail(f"{where}: no CTAs — the write call gets a register with no "
                 "examples, which is where invented asks come from")

    today = date.today()
    phase = campaign.phase_for(today)
    if phase is None:
        warn(f"today ({today}) is outside every phase window — the campaign is "
             "armed but builds would run off-campaign. Check launch_date.")
    else:
        ok(f"writing for '{phase.key}' · the ask: {phase.cta_style or '—'}")

    # The written content, and whether it routes anywhere real.
    posts = load_launch_backlog()
    if not posts:
        warn("config/launch_backlog.toml: no planned posts — `chrgd campaign "
             "seed` would queue nothing")
        return
    phase_keys = {p.key for p in campaign.phases}
    seen: set[str] = set()
    before = len(FAILURES)
    for post in posts:
        where = f"config/launch_backlog.toml [{post.key}]"
        if post.key in seen:
            fail(f"{where}: duplicate key — seeding dedupes on the note, so "
                 "one of these is unreachable")
        seen.add(post.key)
        if not post.concept_note().strip():
            fail(f"{where}: no hook and no note — seeding would skip it")
        if post.phase and post.phase not in phase_keys:
            fail(f"{where}: phase '{post.phase}' is not in campaign.toml")
        if post.show and get_show(post.show) is None:
            fail(f"{where}: show '{post.show}' does not exist — it would build "
                 "as a generic post")
        if post.mechanic and get_mechanic(post.mechanic) is None:
            fail(f"{where}: mechanic '{post.mechanic}' does not exist — the "
                 "format lock would be dropped silently")
        if post.ingredient and get_ingredient(post.ingredient) is None:
            fail(f"{where}: ingredient '{post.ingredient}' is not in the library")
    if len(FAILURES) == before:
        counts: dict[str, int] = {}
        for post in posts:
            counts[post.phase] = counts.get(post.phase, 0) + 1
        ok(f"{len(posts)} planned posts route cleanly · "
           + " ".join(f"{k}:{n}" for k, n in counts.items()))


# --- the box itself ---------------------------------------------------------


def check_render_prerequisites() -> None:
    section("Rendering")
    from chrgd.brand import load_brand

    brand = load_brand()
    for which in ("headline", "supporting"):
        path = brand.resolve_font(which)
        if not Path(path).exists():
            fail(f"brand.toml [fonts] {which}: '{path}' is not on this box — "
                 "the dry-run preview and any overlay render would crash")
    ok(f"fonts resolve · canvas {brand.canvas.width}x{brand.canvas.height} "
       f"{brand.canvas.format}")
    if brand.canvas.format.lower() == "png":
        fail("brand.toml [canvas] format = png — TikTok's API rejects PNG")


def check_settings() -> None:
    section("Settings & secrets")
    from chrgd.config import get_settings

    s = get_settings()
    if not s.openai_api_key:
        warn("CHRGD_OPENAI_API_KEY / OPENAI_API_KEY not set — the studio will "
             "serve, but nothing can be built or rendered")
    else:
        ok("OpenAI key present")

    if not (s.web_password or s.web_password_hash):
        fail("no CHRGD_WEB_PASSWORD or CHRGD_WEB_PASSWORD_HASH — the app would "
             "be unprotected on the public internet")
    elif s.web_password and s.web_password.strip().lower() in (
        "change-me", "changeme", "password", "s3cret", "admin"
    ):
        fail("CHRGD_WEB_PASSWORD is still an example value")
    else:
        ok("web login configured")

    if not s.secret_key:
        fail("no CHRGD_SECRET_KEY — session cookies would not be signed")
    elif len(s.secret_key) < 32:
        fail(f"CHRGD_SECRET_KEY is {len(s.secret_key)} chars — use 32+")
    else:
        ok("session secret set")

    for label, path in (("database", s.db_path.parent), ("output", s.output_dir)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".preflight"
            probe.write_text("ok")
            probe.unlink()
            ok(f"{label} dir writable: {path}")
        except OSError as exc:
            fail(f"{label} dir not writable ({path}): {exc}")

    if s.video_enabled:
        if not shutil.which("ffmpeg"):
            fail("CHRGD_VIDEO_ENABLED is true but ffmpeg is not on PATH")
        if not s.higgsfield_api_key:
            fail("CHRGD_VIDEO_ENABLED is true but no HIGGSFIELD_API_KEY")
        if not FAILURES:
            ok("video enabled and its prerequisites are present")
    else:
        ok("video disabled (no paid video call is reachable)")

    ok(f"spend cap per run: ${s.max_spend_per_run:g}")


def check_app_boots() -> None:
    section("Application")
    try:
        from chrgd.config import get_settings
        from chrgd.webapp import create_app

        app = create_app(get_settings())
        routes = {getattr(r, "path", "") for r in app.routes}
        for path in ("/create", "/calendar", "/api/shows", "/api/roster",
                     "/api/canon", "/api/shows/performance"):
            if path not in routes:
                fail(f"route {path} is missing")
        ok(f"app builds · {len(routes)} routes")
    except Exception as exc:  # noqa: BLE001
        fail(f"the app failed to build: {type(exc).__name__}: {exc}")


def main() -> int:
    print("CHRGD Content Studio — pre-flight (offline, no paid calls)")
    for check in (check_shows, check_roster, check_content_libraries,
                  check_gate_profiles, check_campaign,
                  check_render_prerequisites, check_settings, check_app_boots):
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            fail(f"{check.__name__} blew up: {type(exc).__name__}: {exc}")

    print()
    if FAILURES:
        print(f"NOT READY — {len(FAILURES)} problem(s):")
        for f in FAILURES:
            print(f"  · {f}")
    if WARNINGS:
        print(f"\n{len(WARNINGS)} warning(s) (not blocking):")
        for w in WARNINGS:
            print(f"  · {w}")
    if not FAILURES:
        print("READY — start the service, then run deploy/diagnose.py to test "
              "the paid path.")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
