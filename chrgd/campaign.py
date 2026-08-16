"""The campaign layer — what the studio may say about the launch this week.

Every other config in this repo describes a **format**: a show owns a spine and
a look, a mechanic owns a skeleton, an ingredient owns a subject. None of them
describe a **moment**, and a launch is nothing but a moment — the same show has
to do a different job on the Tuesday after launch than it did the Tuesday
before, and neither job is "the show's normal one".

The clumsy fix is to rewrite five shows twice. This module is the cheap one:
one extra block injected into the write call carrying

  * the **phase** today falls in, resolved from `launch_date` and a set of
    day offsets, so moving the launch moves the entire calendar;
  * the **brief** for that phase — what this post is for, and what it must not
    reach for;
  * the **CTA style and menu** the phase is allowed to use, which is the field
    that actually changes between phases (pure comment-bait before launch, the
    domain in plain text during it);
  * the **product facts** the engine may state, and the internal ones it must
    never leak;
  * the **compliance block** for a personalised-recommendation product, which
    carries a risk the rest of the account doesn't — see `[campaign.compliance]`.

**Disarmed is a true pass-through.** `armed = false`, a missing file, or a
malformed one all yield `None` from `active_phase()`, `build_user_message`
appends nothing, and the built post is byte-identical to a pre-campaign one.
That equivalence is asserted in `tests/test_campaign.py` and is the property to
protect when editing this module — it is the same guarantee the show layer
makes, for the same reason: a campaign is a temporary state, and the studio has
to come out the other side of it unchanged.
"""

from __future__ import annotations

import logging
import tomllib
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

CAMPAIGN_FILE = Path(__file__).resolve().parent.parent / "config" / "campaign.toml"

#: Route key the resolved phase is stamped onto, so a built post records which
#: phase it was written under and the calendar can be audited after the fact.
ROUTE_KEY = "campaign_phase"


class Phase(BaseModel):
    """One window of the campaign, defined in days either side of launch."""

    key: str
    label: str = ""
    #: Days relative to launch day (0 = launch day). Inclusive both ends.
    starts: int = 0
    ends: int = 0
    goal: str = ""
    brief: str = ""
    cta_style: str = ""
    ctas: list[str] = Field(default_factory=list)
    banned: list[str] = Field(default_factory=list)
    #: Whether the calendar can resolve to this phase on its own.
    #:
    #: False makes a phase PIN-ONLY: reachable by `pin_phase` or an explicit
    #: editor choice, invisible to date arithmetic. That's what lets a phase
    #: overlap another one's window without making the calendar ambiguous —
    #: `buildup` sits inside `prime` because it is the same content with one
    #: extra line at the end, and which of the two you want is a decision, not
    #: a date.
    on_calendar: bool = True

    def contains(self, offset: int) -> bool:
        return self.starts <= offset <= self.ends


class Strategy(BaseModel):
    audience_reality: str = ""
    the_wedge: str = ""
    the_format_is_the_product: str = ""

    def as_lines(self) -> list[str]:
        return [
            block.strip()
            for block in (
                self.audience_reality,
                self.the_wedge,
                self.the_format_is_the_product,
            )
            if block.strip()
        ]


class Facts(BaseModel):
    true: list[str] = Field(default_factory=list)
    never_say: list[str] = Field(default_factory=list)


class Compliance(BaseModel):
    block: str = ""


class Tech(BaseModel):
    """How the product's own machinery may be described.

    Its own block because the risk here is different from the health-claim
    risk: it is a claim about US rather than about a body, and the failure
    mode is a false statement about our own product plus an unsubstantiated
    superiority claim — both of which sound like marketing rather than like
    something to be careful with, which is exactly why they need a rule.
    """

    uses_ai: bool = False
    how_it_works: str = ""
    block: str = ""

    def as_block(self) -> str:
        lines = []
        if self.block.strip():
            lines.append(self.block.strip())
        if self.how_it_works.strip():
            lines.append(
                "HOW IT ACTUALLY WORKS — the description to reach for when a "
                f"post talks about the mechanism: {self.how_it_works.strip()}"
            )
        lines.append(
            "THIS PRODUCT USES AI: "
            + ("yes — you may say so, but say what it does rather than that "
               "it exists. 'AI-powered' on its own is a boast, not a benefit."
               if self.uses_ai else
               "NO. The words AI, machine learning, neural, and "
               "algorithm-that-learns are BANNED — using them would be a false "
               "claim about our own product.")
        )
        return "\n\n".join(lines)


class Campaign(BaseModel):
    armed: bool = False
    key: str = ""
    label: str = ""
    launch_date: date | None = None
    what: str = ""
    domain: str = ""
    handle: str = ""
    strategy: Strategy = Field(default_factory=Strategy)
    facts: Facts = Field(default_factory=Facts)
    compliance: Compliance = Field(default_factory=Compliance)
    tech: Tech = Field(default_factory=Tech)
    phases: list[Phase] = Field(default_factory=list)
    #: Shows this campaign doesn't use — dimmed on the create screen, never
    #: disabled. A statement about what the fortnight is for, not a lock.
    paused_shows: list[str] = Field(default_factory=list)
    #: Run in this phase regardless of the calendar.
    #:
    #: A launch date is usually the last thing to get fixed, and the phases are
    #: all defined as offsets from one — so before there is a date the campaign
    #: would resolve to no phase at all and every build would quietly run
    #: off-campaign. Pinning is the answer to "we're launching soon-ish": the
    #: studio writes `prime` content until somebody says when, then you set
    #: `launch_date`, clear this, and the calendar takes over.
    #:
    #: A pin always wins over the calendar, so a stale pin is visible rather
    #: than subtle — `chrgd campaign status` and the create screen both say
    #: they're pinned.
    pin_phase: str = ""

    @field_validator("launch_date", mode="before")
    @classmethod
    def _blank_date_is_no_date(cls, value):
        """`launch_date = ""` means "not decided yet", not a parse error.

        Leaving the key present and empty is how the config says there is no
        date — clearer than deleting the line, and it keeps the field visible
        as the thing to fill in.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # --- resolving the moment ----------------------------------------------

    def day_offset(self, today: date) -> int | None:
        """Days from launch: negative before, 0 on the day, positive after."""
        if self.launch_date is None:
            return None
        return (today - self.launch_date).days

    def phase_for(self, today: date) -> Phase | None:
        """The phase we're in, or None if the campaign isn't running.

        A pinned phase wins outright — that's the mode the studio runs in
        before a launch date exists. Otherwise the first phase whose window
        contains the offset wins, so overlapping windows resolve by file order
        rather than raising: a config mistake should cost a slightly wrong
        brief, never a failed build.
        """
        if not self.armed or not self.phases:
            return None
        if self.pin_phase:
            return self.get_phase(self.pin_phase)
        offset = self.day_offset(today)
        if offset is None:
            return None
        for phase in self.phases:
            if phase.on_calendar and phase.contains(offset):
                return phase
        return None

    def calendar_phases(self) -> list[Phase]:
        """The phases date arithmetic can reach — what must stay contiguous."""
        return [p for p in self.phases if p.on_calendar]

    def get_phase(self, key: str) -> Phase | None:
        """A phase by key, ignoring the calendar — what `--phase` seeds from."""
        for phase in self.phases:
            if phase.key == key:
                return phase
        return None

    # --- what the write call is told ---------------------------------------

    def brief_block(self, phase: Phase, *, today: date | None = None) -> str:
        """The campaign block injected into a build's user message.

        Deliberately one block rather than a second system prompt: the shows,
        the JSON contract and the QA rubric are all unchanged by a campaign,
        and a parallel prompt file would drift from the real one inside a month
        — exactly the reasoning behind `Show.brief_block`.
        """
        when = self._when(today)
        lines = [
            f"THE CAMPAIGN — this post is being written{when} in the "
            f"**{phase.label or phase.key}** phase. That changes what this "
            "post is FOR. It does not change the show's format, its voice, or "
            "the quality bar.",
        ]
        if self.what:
            lines.append("")
            lines.append(f"WHAT WE ARE LAUNCHING: {self.what}")

        strategy = self.strategy.as_lines()
        if strategy:
            lines.append("")
            lines.append(
                "WHY THE CAMPAIGN IS SHAPED THIS WAY — read this before you "
                "write the CTA; the ask below only makes sense in light of it:"
            )
            lines.extend(f"\n{block}" for block in strategy)

        if phase.goal:
            lines.append("")
            lines.append(f"WHAT THIS PHASE IS MEASURED ON: {phase.goal}")
        if phase.brief.strip():
            lines.append("")
            lines.append(phase.brief.strip())

        if phase.cta_style or phase.ctas:
            lines.append("")
            if phase.cta_style:
                lines.append(f"THE ASK, this phase: {phase.cta_style}.")
            if phase.ctas:
                lines.append(
                    "CTAs that fit the phase — match this register, and write "
                    "a better one if you can rather than copying these:"
                )
                lines.extend(f"  - {cta}" for cta in phase.ctas)

        if phase.banned:
            lines.append("")
            lines.append(
                "BANNED IN THIS PHASE — any of these makes the post wrong for "
                "the moment, however good it is otherwise: "
                + "; ".join(phase.banned)
                + "."
            )

        if self.facts.true:
            lines.append("")
            lines.append(
                "THE ONLY PRODUCT FACTS YOU MAY STATE. Anything beyond this "
                "list is invented, and an invented fact about a product that "
                "goes on sale in days is a refund, not a typo:"
            )
            lines.extend(f"  - {fact}" for fact in self.facts.true)
        if self.facts.never_say:
            lines.append("")
            lines.append(
                "NEVER SAY, anywhere — not on a slide, not in the caption, "
                "not in the comment trigger: "
                + "; ".join(self.facts.never_say)
                + "."
            )

        tech = self.tech.as_block()
        if tech.strip():
            lines.append("")
            lines.append(tech)

        if self.compliance.block.strip():
            lines.append("")
            lines.append(self.compliance.block.strip())

        return "\n".join(lines)

    def _when(self, today: date | None) -> str:
        """' 4 days before launch,' — the offset in words, for the brief.

        Empty when there is no launch date, and deliberately so: the phase
        already tells the engine everything it needs, and inventing a timing
        clause to fill the gap would put "days to go" in copy at a point when
        nobody knows how many. Leads with a space and ends without one so the
        sentence reads either way.
        """
        if today is None or self.launch_date is None:
            return ""
        offset = self.day_offset(today)
        if offset is None:
            return ""
        if offset == 0:
            return " on LAUNCH DAY itself,"
        if offset < 0:
            return f" {abs(offset)} day(s) BEFORE launch,"
        return f" {offset} day(s) AFTER launch,"


#: What an unarmed studio resolves to — every field empty, nothing injected.
DISARMED = Campaign()


@lru_cache(maxsize=1)
def load_campaign(path: Path = CAMPAIGN_FILE) -> Campaign:
    """The campaign, or the disarmed default.

    Permissive in exactly the way `shows.load_shows` and
    `mechanics.load_mechanics` are: a missing or broken file leaves the studio
    behaving as though no campaign was ever configured, because a content run
    failing on a malformed TOML block is a far worse outcome than a run that
    quietly writes off-campaign posts a human will notice immediately.
    """
    if not path.exists():
        return DISARMED
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        log.warning("campaign config at %s is unreadable — running disarmed", path)
        return DISARMED
    spec = dict(data.get("campaign") or {})
    if not spec:
        return DISARMED
    try:
        return Campaign(**spec)
    except Exception:  # pragma: no cover - defensive, same reasoning as above
        log.warning("campaign config at %s is invalid — running disarmed", path)
        return DISARMED


def _today() -> date:
    return datetime.now().date()


def active_phase(today: date | None = None) -> Phase | None:
    """The phase the studio is in right now, or None when disarmed/lapsed."""
    return load_campaign().phase_for(today or _today())


def campaign_block(
    today: date | None = None, *, phase_key: str = ""
) -> tuple[str, str]:
    """``(phase_key, brief)`` for the write call — ``("", "")`` when disarmed.

    `phase_key` forces a phase regardless of the date, which is what lets the
    editor batch next week's launch posts today. An unknown key resolves to
    the calendar's answer rather than to nothing, so a typo degrades to correct
    behaviour instead of silently dropping the campaign.
    """
    campaign = load_campaign()
    if not campaign.armed:
        return ("", "")
    when = today or _today()
    forced = campaign.get_phase(phase_key) if phase_key else None
    phase = forced or campaign.phase_for(when)
    if phase is None:
        return ("", "")
    # A forced phase is dated from the phase, not from today. Otherwise a
    # launch post batched a fortnight early opens "written 15 days BEFORE
    # launch, in the Launch week phase" — a contradiction in the first line of
    # the brief, and the engine has to resolve it somehow.
    if forced is not None and campaign.launch_date is not None:
        when = campaign.launch_date + timedelta(days=phase.starts)
    return (phase.key, campaign.brief_block(phase, today=when))


# --- the written backlog ----------------------------------------------------
#
# `config/launch_backlog.toml` holds the campaign's actual content: the posts
# themselves, each already assigned a show, a mechanic and a phase. Seeding
# them stamps that routing onto the backlog row, so a build knows the format
# and the moment before anybody opens the studio — which is the difference
# between a fortnight of planned content and a fortnight of daily decisions.

LAUNCH_BACKLOG_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "launch_backlog.toml"
)


class LaunchPost(BaseModel):
    """One planned post, as written in `config/launch_backlog.toml`."""

    key: str
    phase: str = ""
    #: Suggested day relative to launch. Ordering only — nothing schedules off it.
    day: int = 0
    show: str = ""
    mechanic: str = ""
    #: STRAIGHT UP's subject, when the post is one of its episodes.
    ingredient: str = ""
    #: VERDICT's ruling, decided here rather than by the engine — the calendar
    #: is where the brand's opinions live, so a planned ruling is part of the
    #: content, not something to re-litigate at build time (chrgd/rulings.py).
    verdict_subject: str = ""
    verdict_ruling: str = ""
    verdict_condition: str = ""
    verdict_reason: str = ""
    #: RECEIPTS' real figure. A planned teardown with no price is not a
    #: planned teardown — seeding one would hand the engine the one job it is
    #: explicitly barred from doing.
    receipt_product: str = ""
    receipt_price: str = ""
    hook: str = ""
    note: str = ""
    category: str = ""
    target_viewer: str = ""
    pain_point: str = ""
    core_tension: str = ""

    def title(self) -> str:
        """The one-line name for a screen or a listing.

        The topical posts carry no hook on purpose — they're scouted on the
        day — so falling back to the raw key put `prime_livewire` on a card a
        human is meant to read. The note's first sentence is what that post is
        actually about, which is the honest label for it.
        """
        if self.hook.strip():
            return self.hook.strip()
        first = self.note.strip().split(".")[0].strip()
        if not first:
            return self.key.replace("_", " ")
        return first if len(first) <= 90 else first[:87].rstrip() + "…"

    def concept_note(self) -> str:
        """The seed text the engine builds from — the hook, then the brief.

        The hook rides inside the note rather than in a field of its own
        because `concept_note` is what every downstream stage reads, and a seed
        that opens with a specific angle beats one that opens with a topic by
        a wider margin than any other single lever on a seed row.
        """
        parts = []
        if self.hook.strip():
            parts.append(f"HOOK DIRECTION (a starting point, not the final "
                         f"headline): {self.hook.strip()}")
        if self.note.strip():
            parts.append(self.note.strip())
        return "\n\n".join(parts)

    def route(self) -> dict:
        """The creation prefs stamped onto the row, read by `creation_prefs`."""
        route: dict = {}
        if self.show:
            route["show"] = self.show
        if self.mechanic:
            # The lock is the resolved mechanic, not its key: the write call
            # reads `name` and `skeleton` straight off route_json and never
            # looks the key up, so an unresolvable key must drop the lock
            # rather than stamp a shape the engine will silently ignore.
            from .mechanics import get_mechanic

            mechanic = get_mechanic(self.mechanic)
            if mechanic is not None:
                route["mechanic_lock"] = {
                    "key": mechanic.key,
                    "name": mechanic.label,
                    "skeleton": list(mechanic.skeleton),
                }
        if self.ingredient:
            route["ingredient"] = self.ingredient
        if self.verdict_subject and self.verdict_ruling:
            from .rulings import RULINGS, VERDICT_KEY

            if self.verdict_ruling in RULINGS:
                route[VERDICT_KEY] = {
                    "subject": self.verdict_subject,
                    "ruling": self.verdict_ruling,
                    "condition": self.verdict_condition,
                    "reason": self.verdict_reason,
                }
        if self.receipt_product and self.receipt_price:
            from .rulings import RECEIPT_KEY

            route[RECEIPT_KEY] = {
                "product": self.receipt_product,
                "price": self.receipt_price,
                "note": "",
            }
        if self.phase:
            route[ROUTE_KEY] = self.phase
        return route


@lru_cache(maxsize=1)
def load_launch_backlog(path: Path = LAUNCH_BACKLOG_FILE) -> list[LaunchPost]:
    """The planned posts, in calendar order. Empty when the file is absent."""
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        log.warning("launch backlog at %s is unreadable — skipping", path)
        return []
    posts = [LaunchPost(**spec) for spec in (data.get("posts") or [])]
    return sorted(posts, key=lambda p: p.day)


def planned_posts(phase: str = "") -> list[LaunchPost]:
    """Planned posts, optionally narrowed to one phase."""
    posts = load_launch_backlog()
    return [p for p in posts if p.phase == phase] if phase else posts


def seed_posts(store, settings, posts: list[LaunchPost]) -> tuple[list, list[str]]:
    """Write planned posts into the backlog as routed seed rows.

    Returns ``(created, skipped)``. Deduping is `capture`'s — matched on the
    concept note — so re-running a seed after adding three posts to the file
    queues the three and leaves the rest alone, which is the behaviour you want
    when the calendar is edited mid-campaign rather than written once.
    """
    import json

    from .models import Idea, Status

    created, skipped = [], []
    for post in posts:
        note = post.concept_note()
        if not note.strip():
            continue
        if store.find_by_concept_note(note):
            skipped.append(post.key)
            continue
        route = post.route()
        idea = Idea(
            idea_id=store.next_idea_id(settings.id_prefix),
            status=Status.queued,
            # The campaign is the priority while it's running.
            priority=1,
            content_category=post.category,
            target_viewer=post.target_viewer,
            pain_point=post.pain_point,
            core_tension=post.core_tension,
            concept_note=note,
            learning_tag=f"campaign:{post.phase}" if post.phase else "",
            route_json=json.dumps(route) if route else None,
        )
        created.append(store.add_idea(idea))
    return created, skipped


def status_lines(today: date | None = None) -> list[str]:
    """Human-readable campaign state — what `chrgd campaign status` prints."""
    campaign = load_campaign()
    when = today or _today()
    if not campaign.armed:
        return ["Campaign: disarmed. The studio is writing off-campaign posts."]
    out = [f"Campaign: {campaign.label or campaign.key}"]
    if campaign.launch_date:
        offset = campaign.day_offset(when) or 0
        when_word = (
            "launch day" if offset == 0
            else f"{abs(offset)} days to go" if offset < 0
            else f"{offset} days since launch"
        )
        out.append(f"Launch:   {campaign.launch_date}  ({when_word})")
    else:
        out.append("Launch:   not set yet")
    phase = campaign.phase_for(when)
    if phase and campaign.pin_phase:
        out.append(
            f"Phase:    {phase.label or phase.key}  (PINNED — ignoring the "
            "calendar until a launch date is set)"
        )
    elif phase:
        out.append(f"Phase:    {phase.label or phase.key}")
    else:
        out.append(
            "Phase:    none — no launch date and no pinned phase, so builds "
            "are running OFF-CAMPAIGN"
        )
    if phase:
        out.append(f"Goal:     {phase.goal}")
        out.append(f"The ask:  {phase.cta_style}")
    return out
