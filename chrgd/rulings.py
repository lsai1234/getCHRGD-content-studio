"""What the editor decided — VERDICT's ruling and RECEIPTS' price.

Every other show hands the engine a subject and lets it find the angle. These
two can't work that way, and the reason is the same for both: the thing that
makes the post worth reading is a **fact or a judgement the business owns**,
and neither is something a language model should be producing.

* **VERDICT** rules on whether a purchase is worth somebody's money. That
  opinion has to be the brand's — it is the thing a customer would quote back
  at you. So the editor picks the ruling on the show's screen and the write
  call is told to execute it, not to arrive at one.
* **RECEIPTS** breaks a real price down. Asked for "a typical tub price" a
  model will produce a figure that sounds right and isn't, and a teardown
  built on an invented number is worthless — and, on a supplement account,
  a genuine problem. So the editor types the real figure and the engine is
  told it may use that one and no other.

The shape is deliberately the same in both cases: **the human owns the facts
and the judgement, the engine owns the craft.** These blocks are what carry
that into the prompt.

Both ride `route_json` like every other creation pref, so a rebuild keeps the
ruling it was written under rather than quietly re-deciding it.
"""

from __future__ import annotations

#: Route keys these ride on.
VERDICT_KEY = "verdict"
RECEIPT_KEY = "receipt"

#: The rulings VERDICT can hand down. `only_if` carries a condition.
RULINGS: dict[str, str] = {
    "worth_it": "WORTH IT",
    "not_worth_it": "NOT WORTH IT",
    "only_if": "ONLY IF",
}


def verdict_brief(verdict: dict) -> str:
    """VERDICT's brief — the editor's ruling, stated as non-negotiable."""
    subject = str(verdict.get("subject") or "").strip()
    ruling = str(verdict.get("ruling") or "").strip()
    if not subject or ruling not in RULINGS:
        return ""

    lines = [
        f"THE SUBJECT OF THIS RULING: {subject}.",
        "",
        f"THE EDITOR'S RULING — this is the post's verdict and it is NOT "
        f"yours to revisit: **{RULINGS[ruling]}**.",
    ]
    condition = str(verdict.get("condition") or "").strip()
    if ruling == "only_if":
        lines.append(
            "This is a conditional ruling, so the condition must appear in "
            "the opening slide alongside it — an 'only if' whose condition "
            "arrives on slide 3 reads as a hedge, which is the one thing this "
            "show cannot do."
        )
        if condition:
            lines.append(f"THE CONDITION: {condition}")
    elif condition:
        lines.append(f"THE EDITOR'S NOTE ON THE RULING: {condition}")

    reason = str(verdict.get("reason") or "").strip()
    if reason:
        lines.append("")
        lines.append(
            f"THE REASONING THE EDITOR WANTS MADE — build the case around "
            f"this rather than around one you prefer: {reason}"
        )

    lines.append("")
    lines.append(
        "EXECUTE THIS RULING. Build the strongest honest case for it. Do not "
        "soften it, do not balance it, do not quietly turn it into an "
        "overview of both sides. If it genuinely cannot be defended inside "
        "the claims rules, say so plainly in your QA reasoning rather than "
        "writing around it — a hedged verdict is worse than a flagged one."
    )
    if ruling == "not_worth_it":
        lines.append(
            "This ruling goes AGAINST something the shop could sell, which is "
            "exactly why it is worth posting. Do not pull the punch, and do "
            "not add a consoling paragraph at the end that gives the thing "
            "back its credibility."
        )
    return "\n".join(lines)


def receipt_brief(receipt: dict) -> str:
    """RECEIPTS' brief — the one real figure, and the ban on inventing more."""
    product = str(receipt.get("product") or "").strip()
    price = str(receipt.get("price") or "").strip()
    if not product or not price:
        return ""

    lines = [
        f"WHAT IS BEING TORN DOWN: {product}.",
        f"THE PRICE — the real figure, supplied by the editor: {price}.",
        "",
        "THIS IS THE ONLY FIGURE YOU HAVE. You may state it, and you may do "
        "arithmetic on it that is honestly true (a half, a third, per serving "
        "if a serving count is given above). You may NOT introduce a second "
        "price, a percentage, a margin, an industry average, or a 'typically "
        "around' anything. If a slide seems to need a number you were not "
        "given, rewrite the slide to work without one — proportions, "
        "mechanisms and plain description carry it perfectly well.",
        "",
        "An invented number on a price teardown destroys the only thing this "
        "show has, and on a supplement account it is a real problem rather "
        "than an inaccuracy. Treat it as a hard stop.",
    ]
    note = str(receipt.get("note") or "").strip()
    if note:
        lines.append("")
        lines.append(f"THE EDITOR'S ANGLE ON THIS ONE: {note}")
    lines.append("")
    lines.append(
        "NAME NO BRAND. The subject is the category's pricing model — the "
        "blend, the flavouring, the label, the sponsorship, the shelf space. "
        "Whose tub it is never matters and must never appear."
    )
    return "\n".join(lines)
