"""Today's pick (Bet 3) — the app opens on ONE strong, ready recommendation
instead of a blank door.

At one post a day you don't need a week-planner; you need "here's today's strong
pick," singular. This module reuses the discovery scans that the create screen
already warms on open (moments / trending / evergreen / ragebait) — no new scan,
no web search — gathers their surfaced stories as candidates, and makes ONE cheap
LLM call to choose today's single strongest UK concept plus a couple of
alternates. It is concept/text only: it renders nothing and never touches the
image budget.

Grounded, not invented: the selector may only pick from real scanned candidates
(by index) and shape how they're framed — it can't hallucinate a story. If the
LLM or its JSON fails, a deterministic heuristic still returns a pick, so the
card always has something when scans exist.

The selector is injectable so tests run offline with no key.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import BaseModel, ValidationError

from .config import Settings
from .db import Store

# The lanes we harvest, in priority order — a live UK moment beats an evergreen
# fact when everything else is equal, and the heuristic fallback leans on this.
_LANES = ("moments", "trending", "ragebait", "evergreen")

# How many candidates we hand the selector — enough range to choose well, few
# enough to keep the prompt (and its cost) small.
_MAX_CANDIDATES = 12


class TodaysPickError(RuntimeError):
    pass


class PickSelector(Protocol):
    def select(self, system: str, user: str) -> str:
        """Run a JSON completion choosing today's pick; return the text."""
        ...


class OpenAIPickSelector:
    """Today's pick via the cheap scout model — this is selection + framing, not
    shipped copy, so it doesn't need the creative model."""

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise TodaysPickError("OPENAI_API_KEY is not set — add it to your .env")
        from .llm import text_client

        try:
            self._client = text_client(settings)
        except ImportError as exc:  # pragma: no cover
            raise TodaysPickError(
                "openai not installed. Run: pip install -e '.[llm]'"
            ) from exc
        self._model = settings.scout_model

    def select(self, system: str, user: str) -> str:
        from .pipeline import _describe_llm_error, chat_json_create

        try:
            resp = chat_json_create(
                self._client, model=self._model, system=system, user=user,
                temperature=0.4,
            )
        except Exception as exc:  # noqa: BLE001
            raise TodaysPickError(_describe_llm_error(exc)) from exc
        return resp.choices[0].message.content or ""


# --- candidate gathering (reuses scans already run — no new scan) --------------


class Candidate(BaseModel):
    lane: str
    job_id: int
    moment: int          # index of the story within its scan
    title: str
    why: str = ""
    category: str = ""
    angle: str = ""       # the strongest ready angle, if the scan carried one


def _latest_completed(store: Store, kind: str) -> dict | None:
    return store.conn.execute(
        "SELECT job_id, result_json FROM jobs WHERE kind = ? AND status = 'COMPLETED' "
        "ORDER BY job_id DESC LIMIT 1",
        (kind,),
    ).fetchone()


def _best_angle(moment: dict) -> str:
    """A one-line angle from a scanned story, if it carried any (two-stage scans
    surface headlines first, so this is often empty — that's fine)."""
    for a in moment.get("angles") or []:
        note = (a.get("concept_note") or a.get("title") or "").strip()
        if note:
            return note + (f" — open with: {a['hook']}" if a.get("hook") else "")
    return ""


def gather_candidates(store: Store, limit: int = _MAX_CANDIDATES) -> list[Candidate]:
    """Flatten the latest completed scan per lane into a ranked candidate pool.

    Round-robins across lanes so one noisy lane can't crowd the others out, and
    keeps each candidate's source job so a one-tap build reuses the real scan."""
    per_lane: dict[str, list[Candidate]] = {}
    for lane in _LANES:
        row = _latest_completed(store, lane)
        if not row:
            continue
        try:
            moments = json.loads(row["result_json"] or "{}").get("moments", [])
        except json.JSONDecodeError:
            continue
        picks = []
        for i, m in enumerate(moments):
            title = (m.get("title") or "").strip()
            if not title:
                continue
            picks.append(
                Candidate(
                    lane=lane, job_id=row["job_id"], moment=i, title=title,
                    why=(m.get("why") or "").strip(),
                    category=(m.get("category") or "").strip(),
                    angle=_best_angle(m),
                )
            )
        if picks:
            per_lane[lane] = picks

    # Round-robin by lane priority until we hit the limit.
    out: list[Candidate] = []
    idx = 0
    while len(out) < limit and any(idx < len(v) for v in per_lane.values()):
        for lane in _LANES:
            if idx < len(per_lane.get(lane, [])):
                out.append(per_lane[lane][idx])
                if len(out) >= limit:
                    break
        idx += 1
    return out


# --- the selection prompt ------------------------------------------------------

SELECT_SYSTEM = """You are the head content strategist for CHRGD, a premium UK gym/supplement TikTok brand posting ONE carousel a day. The account is small and every post has to earn its reach.

You are shown a numbered list of REAL story candidates already scouted today (live UK moments, trending formats, arguments, evergreen facts), plus the brand's content pillars, what has actually worked for this account, and the UK cultural calendar. Choose the SINGLE strongest post to make TODAY, and a couple of alternates.

Rules:
- Pick ONLY from the numbered candidates — reference them by their exact index. Never invent a story that isn't listed.
- Favour a live, specifically-UK moment a stranger would already feel, on-pillar, and matching what has hit for this account. A timeless evergreen fact is a weaker "today" pick than a moment that's live right now.
- REJECT generic calendar filler. An awareness day/week/month or heritage month (Black History Month, Pride, Mental Health Awareness Week, Earth Day, and the rest) or any bland "a celebration of X with various events" entry is the WORST pick — it's what every corporate account posts, it's invisible, and for a gym/supplement brand it reads as hollow box-ticking. Only ever choose one if it's genuinely live this week AND carries a sharp, specific, unmistakably-gym angle. Otherwise pick a real, sharper candidate instead — even a good evergreen fact or a trending format beats a dead calendar holiday.
- Think outside the box: favour the specific, slightly-unexpected, thumb-stopping angle a cynical 18-30 UK gym viewer would actually stop for, not the safe obvious one.
- Frame slide 1 concretely (slide 1 is ~90% of reach): the opener idea AND what the image shows. Keep it native — a candid phone photo / screenshot / meme, never an advert.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "pick_index": integer — the chosen candidate's index,
  "why_today": "one line: why THIS is the strongest post to make today",
  "build_angle": "the one concrete UK-native angle to build from (one line)",
  "slide1_concept": "the slide-1 opener — the hook idea + what the image shows",
  "cast": "who is in slide 1 in one short phrase (a freshly-cast aspirational gym-goer, or the recurring person), or an empty string",
  "alternate_indices": [up to two other candidate indices worth a look]
}"""


def _candidate_lines(cands: list[Candidate]) -> str:
    rows = []
    for i, c in enumerate(cands):
        bits = [f"[{i}] ({c.lane}) {c.title}"]
        if c.why:
            bits.append(f"— {c.why}")
        if c.angle:
            bits.append(f"[ready angle: {c.angle}]")
        rows.append(" ".join(bits))
    return "\n".join(rows)


def _steering(store: Store) -> str:
    """Pillars + what's hit for this account + the UK calendar — the context that
    turns 'a good post' into 'today's post for THIS account'."""
    from .learning import insights
    from .profile import load_profile
    from .uk import uk_calendar_seed

    lines: list[str] = []
    profile = load_profile(store)
    pillars = [p.strip() for p in (profile.pillars or "").splitlines() if p.strip()]
    if pillars:
        lines.append("CONTENT PILLARS (stay in a lane): " + " · ".join(pillars))
    hit = insights(store).get("hit_traits", [])
    if hit:
        top = ", ".join(f"{t['value']} ({t['trait']})" for t in hit[:3])
        lines.append(f"WHAT HAS HIT FOR THIS ACCOUNT: {top} — lean this way where it fits.")
    seed = uk_calendar_seed()
    if seed:
        lines.append(seed)
    return "\n".join(lines)


class PickChoice(BaseModel):
    pick_index: int = 0
    why_today: str = ""
    build_angle: str = ""
    slide1_concept: str = ""
    cast: str = ""
    alternate_indices: list[int] = []


def _parse_choice(text: str) -> PickChoice:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise TodaysPickError("no JSON object in the pick response")
    try:
        return PickChoice.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise TodaysPickError(f"could not parse the pick: {exc}") from exc


def _as_card(cand: Candidate, *, why: str = "", angle: str = "",
             slide1: str = "", cast: str = "") -> dict:
    """A candidate → the UI/build payload. `build_angle` is what a one-tap build
    sends as the custom angle to the existing moment-use endpoint."""
    build_angle = (angle or cand.angle or cand.title).strip()
    return {
        "lane": cand.lane,
        "job_id": cand.job_id,
        "moment": cand.moment,
        "title": cand.title,
        "why_today": (why or cand.why).strip(),
        "build_angle": build_angle,
        "slide1_concept": slide1.strip(),
        "cast": cast.strip(),
    }


def todays_pick(
    store: Store, settings: Settings, *, selector: PickSelector | None = None
) -> dict:
    """Today's single strongest pick (+ alternates) from already-warmed scans.

    Never raises: if scans haven't landed yet it reports `warming`; if the LLM
    or its JSON fails it falls back to the top heuristic candidate so the card
    always has something to show when candidates exist."""
    cands = gather_candidates(store)
    if not cands:
        return {"status": "warming", "pick": None, "alternates": []}

    try:
        selector = selector or OpenAIPickSelector(settings)
        user = (
            "Story candidates scouted today:\n"
            + _candidate_lines(cands)
            + "\n\n"
            + _steering(store)
            + "\n\nChoose today's single strongest post and up to two alternates. "
            "Return the JSON object."
        )
        choice = _parse_choice(selector.select(SELECT_SYSTEM, user))
        if not 0 <= choice.pick_index < len(cands):
            raise TodaysPickError("pick_index out of range")
        pick = _as_card(
            cands[choice.pick_index], why=choice.why_today,
            angle=choice.build_angle, slide1=choice.slide1_concept, cast=choice.cast,
        )
        alternates = [
            _as_card(cands[i])
            for i in choice.alternate_indices
            if 0 <= i < len(cands) and i != choice.pick_index
        ][:2]
        return {"status": "ready", "pick": pick, "alternates": alternates,
                "shaped": True}
    except (TodaysPickError, ValidationError, json.JSONDecodeError):
        # Heuristic fallback: the top candidate by lane priority, unshaped.
        return {
            "status": "ready",
            "pick": _as_card(cands[0]),
            "alternates": [_as_card(c) for c in cands[1:3]],
            "shaped": False,
        }
