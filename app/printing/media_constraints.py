"""Which paper sizes each media type allows, per printer model.

Some printers reject an invalid media type + paper size pair at print time
(Canon: support code 4102, the job hangs until someone presses Stop), and
their drivers don't expose the rule through any API — DocumentProperties and
PrintTicket validation both accept anything; only the driver's own dialog
enforces it. So the rules live here as data, keyed by driver name, using
DEVMODE ids (dmMediaType / dmPaperSize), which don't depend on the Windows
language. Printers not listed are passed through unchanged."""

from dataclasses import dataclass

# DEVMODE paper ids used below
LETTER, LEGAL, A4, A5, B5 = 1, 5, 9, 11, 13
ENV_COM10, ENV_DL = 20, 27
PHOTO_4X6, PHOTO_5X7 = 119, 120


@dataclass(frozen=True)
class MediaRule:
    kind: str  # "plain" | "photo" | "envelope" — used to pick a similar replacement
    sizes: frozenset[int] | None  # allowed dmPaperSize ids; None = any


# Canon MG2500 series (MG2540/MG2541/MG2545...).
# Plain paper and envelopes: "Media Types You Can Use" in Canon's online manual;
# photo papers: sizes the Canon papers are sold in (GP-501/GP-601: A4, Letter,
# 4x6; PP-201: A4, Letter, 4x6, 5x7). The driver's "(Масштабирование)" sizes
# are scaled onto regular paper and allowed with plain paper only.
_CANON_MG2500 = {
    1: MediaRule("plain", None),  # Plain Paper
    303: MediaRule("photo", frozenset({A4, LETTER, PHOTO_4X6, PHOTO_5X7})),  # Photo Paper Plus Glossy II
    277: MediaRule("photo", frozenset({A4, LETTER, PHOTO_4X6})),  # Glossy Photo Paper
    263: MediaRule("envelope", frozenset({ENV_COM10, ENV_DL})),  # Envelope
}

RULES: dict[str, dict[int, MediaRule]] = {
    "Canon MG2500 series Printer": _CANON_MG2500,
}


def rules_for(driver_name: str) -> dict[int, MediaRule] | None:
    return RULES.get(driver_name)


def allowed(rules: dict[int, MediaRule], media_id: int, paper_id: int) -> bool:
    rule = rules.get(media_id)
    return rule is None or rule.sizes is None or paper_id in rule.sizes


def resolve(rules: dict[int, MediaRule], media_id: int, paper_id: int) -> int:
    """Media type to use for this paper size: the requested one if allowed,
    else the most similar allowed one (same kind first, then plain paper) —
    the size is what the content was laid out for, so it's kept."""
    if allowed(rules, media_id, paper_id):
        return media_id
    kind = rules[media_id].kind
    candidates = sorted(rules, key=lambda m: (rules[m].kind != kind, rules[m].kind != "plain"))
    for candidate in candidates:
        if allowed(rules, candidate, paper_id):
            return candidate
    return media_id  # nothing fits — leave it to the printer
