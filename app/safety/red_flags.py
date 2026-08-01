"""Pure coded red-flag rules. This module has no model dependency."""

import re
from dataclasses import dataclass

from app.schemas.enums import (
    AppetiteChange,
    BodySystem,
    CatSex,
    UrgencyTier,
    VomitingFrequency,
)
from app.schemas.llm import SymptomIntake
from app.tools.contracts import (
    RedFlagChecker,
    RedFlagCheckerInput,
    RedFlagCheckerOutput,
    RedFlagResult,
)


@dataclass(frozen=True)
class CannedSafetyResponse:
    """Pre-written response selected by code, never generated."""

    id: str
    severity: UrgencyTier
    text: str
    male_addendum: str | None = None


CANNED_RESPONSES: dict[str, CannedSafetyResponse] = {
    "urinary_obstruction": CannedSafetyResponse(
        id="urinary_obstruction",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Straining to urinate with little or no output can be life-threatening. "
            "Please seek emergency veterinary care immediately."
        ),
        male_addendum=(
            "Male cats are especially vulnerable because their narrower urethra "
            "can obstruct more easily. A complete blockage can become life-threatening "
            "within roughly 24 to 48 hours."
        ),
    ),
    "breathing_difficulty": CannedSafetyResponse(
        id="breathing_difficulty",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Difficulty, laboured, or open-mouth breathing is an emergency. "
            "Please seek emergency veterinary care immediately."
        ),
    ),
    "seizure": CannedSafetyResponse(
        id="seizure",
        severity=UrgencyTier.EMERGENCY,
        text="A seizure is an emergency. Please seek emergency veterinary care immediately.",
    ),
    "collapse": CannedSafetyResponse(
        id="collapse",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Collapse or unresponsiveness is an emergency. "
            "Please seek emergency veterinary care immediately."
        ),
    ),
    "toxin_ingestion": CannedSafetyResponse(
        id="toxin_ingestion",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Suspected toxin ingestion is an emergency. "
            "Please contact an emergency veterinarian or veterinary poison service now."
        ),
    ),
    "human_medication": CannedSafetyResponse(
        id="human_medication",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Human-medication ingestion can be life-threatening to cats. "
            "Please contact an emergency veterinarian or veterinary poison service now."
        ),
    ),
    "lily_exposure": CannedSafetyResponse(
        id="lily_exposure",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Any true-lily exposure can be life-threatening to a cat. "
            "Please seek emergency veterinary care immediately."
        ),
    ),
    "abnormal_gums": CannedSafetyResponse(
        id="abnormal_gums",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Blue, grey, or very pale gums can signal a critical emergency. "
            "Please seek emergency veterinary care immediately."
        ),
    ),
    "not_eating_48h": CannedSafetyResponse(
        id="not_eating_48h",
        severity=UrgencyTier.URGENT,
        text=(
            "No food intake for more than 48 hours needs urgent assessment. "
            "Please contact a veterinarian today."
        ),
    ),
    "vomiting_blood": CannedSafetyResponse(
        id="vomiting_blood",
        severity=UrgencyTier.URGENT,
        text="Vomiting blood needs urgent assessment. Please contact a veterinarian today.",
    ),
    "vomiting_with_lethargy": CannedSafetyResponse(
        id="vomiting_with_lethargy",
        severity=UrgencyTier.URGENT,
        text=(
            "Repeated vomiting with lethargy needs urgent assessment. "
            "Please contact a veterinarian today."
        ),
    ),
    "cannot_bear_weight": CannedSafetyResponse(
        id="cannot_bear_weight",
        severity=UrgencyTier.URGENT,
        text=(
            "Inability to bear weight on a limb needs urgent assessment. "
            "Please contact a veterinarian today."
        ),
    ),
    "painful_eye": CannedSafetyResponse(
        id="painful_eye",
        severity=UrgencyTier.URGENT,
        text=(
            "A cloudy, bulging, or clearly painful eye needs urgent assessment. "
            "Please contact a veterinarian today."
        ),
    ),
}


_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})
_BOUNDARY_PUNCTUATION = re.compile(r"[^\w'\s]+", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

_STANDALONE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "seizure",
        (
            r"\b(?:seizure|seizures|seizing|siezure|convulsion|convulsions|convulsing|fitting)\b",
            r"\b(?:had|having|kind\s+of)\s+(?:some\s+kind\s+of\s+|a\s+)?fit\b",
        ),
    ),
    (
        "collapse",
        (
            r"\b(?:collapsed|collapse|unresponsive|passed out|fainted|blackout)\b",
            r"\b(?:won't|wont)\s+wake\b",
            r"\b(?:not|barely)\s+(?:responsive|alert)\b",
            r"\bunable\s+to\s+wake(?:\s+up)?\b",
            r"\bstopped\s+moving\b",
        ),
    ),
    ("lily_exposure", (r"\b(?:lily|lilies|lilie)\b",)),
    (
        "toxin_ingestion",
        (
            r"\b(?:poison|poisoned|poisonous|poisen|toxic|antifreeze|rodenticide)\b",
        ),
    ),
    (
        "abnormal_gums",
        (
            r"\b(?:blue|bluish|blueish|grey|gray|pale|white|colou?rless)\b.{0,30}\bgums?\b",
            r"\bgums?\b.{0,30}\b(?:blue|bluish|blueish|grey|gray|pale|white|colou?rless)\b",
        ),
    ),
    (
        "human_medication",
        (
            r"\b(?:acetaminophen|paracetamol|tylenol|ibuprofen|advil|aspirin|naproxen)\b",
            r"\bmy\s+(?:pill|pills|medication|medicine|tablet|tablets)\b",
            r"\bhuman\s+(?:pill|pills|medication|medicine|tablet|tablets)\b",
        ),
    ),
)

_ABNORMALITY_QUALIFIERS = (
    r"\b(?:unusual|weird|funny|strange|odd|off|wrong|abnormal|worried)\b",
    r"\bnot\s+right\b",
    r"\b(?:hard|difficult|laboured|labored|heavy|fast|rapid|shallow|noisy)\b",
    r"\b(?:struggling|trouble|unable|stopped|barely|suddenly|started|worse)\b",
    r"\b(?:can't|cant|cannot|won't|wont|isn't|isnt)\b",
)

_RESPIRATORY_DOMAIN = (
    r"\b(?:breath|breaths|breathe|breathes|breathing|breathin)\b",
    r"\brespirat\w*\b",
    r"\bpant(?:s|ed|ing)?\b",
    r"\bwheez\w*\b",
    r"\bgasp\w*\b",
    r"\bchok\w*\b",
    r"\bhyperventilat\w*\b",
)

_COMBINATORIAL_DOMAINS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "urinary_obstruction",
        (
            r"\bpee(?:s|d|ing)?\b",
            r"\burinat\w*\b",
            r"\burine\b",
            r"\blitter\s+box\b",
            r"\blitterbox\b",
            r"\btray\b",
            r"\bwee(?:s|d|ing)?\b",
        ),
    ),
    (
        "not_eating_48h",
        (
            r"\beat(?:s|en|ing)?\b",
            r"\bfood\b",
            r"\bappetite\b",
            r"\bdrink(?:s|ing)?\b",
        ),
    ),
    (
        "collapse",
        (
            r"\bawake\b",
            r"\balert\b",
            r"\bresponsive\b",
            r"\bstanding\b",
            r"\bwalking\b",
            r"\bmoving\b",
        ),
    ),
)

_SPECIFIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "urinary_obstruction",
        (
            r"\b(?:no|little)\s+urine\b",
            r"\bstrain\w*\b.*\b(?:pee|urinate|urination|litter\s+box|litterbox|tray)\b",
            r"\b(?:pee|urinate|urination|litter\s+box|litterbox|tray)\b.*\bstrain\w*\b",
            r"\b(?:pee|urinate|urination)\b.*\b(?:nothing|no output|few drops|only drops)\b",
            r"\b(?:few\s+drops?|dribbl\w*)\b.*\b(?:urine|wee\w*|pee\w*)\b",
            r"\b(?:urine|wee\w*|pee\w*)\b.*\b(?:few\s+drops?|dribbl\w*)\b",
            r"\bback\s+and\s+forth\b.*\b(?:tray|litter\s+box|litterbox)\b.*\bno\s+pee\b",
        ),
    ),
    (
        "human_medication",
        (
            r"\b(?:swallowed|ate|eaten|licked|chewed|got into)\b.*\b(?:pill|tablet|capsule|medication|medicine)\b",
        ),
    ),
    (
        "not_eating_48h",
        (
            r"\b(?:not eaten|hasn't eaten|hasnt eaten|not eating|no food|without food|appetite gone|not touched food|food untouched|no dinner)\b.*\b(?:48\s*(?:h|hr|hrs|hours?)|2\s*days?|two\s*(?:days?|nights?)|(?:3|4|5|6|7|8|9|three|four|five|six|seven|eight|nine)\s*days?|[1-9][0-9]+\s*days?)\b",
            r"\b(?:48\s*(?:h|hr|hrs|hours?)|2\s*days?|two\s*(?:days?|nights?)|(?:3|4|5|6|7|8|9|three|four|five|six|seven|eight|nine)\s*days?)\b.*\b(?:not eaten|not eating|without food|no food|no dinner|food untouched)\b",
            r"\bday\s+(?:3|4|5|6|7|8|9|three|four|five|six|seven|eight|nine)\b.*\b(?:not eating|without food|no food)\b",
        ),
    ),
    (
        "vomiting_blood",
        (
            r"\b(?:vomit\w*|threw up|throwing up|puk\w*|heav\w*|being sick|cat sick)\b.*\b(?:blood\w*|coffee grounds|red stuff)\b",
            r"\b(?:blood\w*|coffee grounds|red stuff)\b.*\b(?:vomit\w*|threw up|throwing up|puk\w*|heav\w*|being sick|cat sick|sick|came up)\b",
        ),
    ),
    (
        "vomiting_with_lethargy",
        (
            r"\b(?:vomit\w*|throwing up|threw up|puk\w*|being sick|sick over and over)\b.*\b(?:letharg\w*|very tired|no energy|listless|barely moves?|won't move|wont move|floppy|drained|weak)\b",
            r"\b(?:letharg\w*|very tired|no energy|listless|barely moves?|won't move|wont move|floppy|drained|weak)\b.*\b(?:vomit\w*|throwing up|threw up|puk\w*|being sick|sick over and over)\b",
        ),
    ),
    (
        "cannot_bear_weight",
        (
            r"\b(?:can't|cant|cannot|unable|won't|wont)\b.*\b(?:bear|put)\s+weight\b",
            r"\b(?:not|isn't|isnt|won't|wont)\s+(?:use|using)\b.*\b(?:leg|limb|paw|foot)\b",
            r"\b(?:leg|limb|paw)\b.*\b(?:can't|cant|cannot|unable|won't|wont)\b.*\b(?:stand|walk|weight)\b",
            r"\b(?:can't|cant|cannot|unable)\b.*\b(?:stand|walk)\b.*\b(?:leg|limb|paw|foot)\b",
            r"\b(?:unable|can't|cant|cannot)\s+to\s+(?:stand|walk)\s+on\b.*\b(?:leg|limb|paw|foot)\b",
            r"\b(?:hold\w*|keep\w*)\b.*\b(?:leg|limb|paw|foot)\b.*\b(?:up|off\s+the\s+floor)\b",
            r"\bnot\s+bearing\s+weight\b",
            r"\b(?:leg|limb|paw|foot)\b.*\bunusable\b",
            r"\b(?:will\s+not|won't|wont)\s+step\s+on\b.*\b(?:leg|limb|paw|foot)\b",
            r"\bunable\s+to\s+put\b.*\b(?:leg|limb|paw|foot)\b.*\bdown\b",
            r"\bwalking\s+on\s+(?:three|3)\s+legs?\b",
            r"\b(?:leg|limb)\b.*\bgives?\s+way\b",
        ),
    ),
    (
        "painful_eye",
        (
            r"\b(?:cloudy|bulging|painful|hazy|milky|sore|swollen|protruding)\b.{0,35}\b(?:eyes?|eyeballs?)\b",
            r"\b(?:eyes?|eyeballs?)\b.{0,35}\b(?:cloudy|bulging|painful|pain|held shut|holding|shut|swollen|hazy|squint\w*|hurt\w*|milky|protruding|sore|closed)\b",
        ),
    ),
)


class DeterministicRedFlagChecker(RedFlagChecker):
    """Coded raw-text and structured-intake rules; no LLM call is possible."""

    async def check(self, request: RedFlagCheckerInput) -> RedFlagCheckerOutput:
        return RedFlagCheckerOutput(result=self.check_intake(request.intake))

    def check_raw(self, raw_text: str) -> RedFlagResult:
        """Normalize and screen owner text before any model can be called."""
        text = _normalize_owner_text(raw_text)
        matched = [
            rule_id
            for rule_id, patterns in _STANDALONE_RULES
            if _matches_any(text, patterns)
        ]

        respiratory = _matches_any(text, _RESPIRATORY_DOMAIN)
        if respiratory:
            # Respiratory distress can become fatal in cats very quickly. Owners
            # rarely volunteer breathing vocabulary in the health corner unless
            # something already looks wrong, so a bare respiratory mention is
            # deliberately enough to escalate. The cost of a false positive is a
            # vet referral; the cost of a false negative can be death. Do not
            # "optimize" this into qualifier-dependent matching.
            matched.append("breathing_difficulty")

        has_qualifier = _matches_any(text, _ABNORMALITY_QUALIFIERS)
        if has_qualifier:
            matched.extend(
                rule_id
                for rule_id, patterns in _COMBINATORIAL_DOMAINS
                if _matches_any(text, patterns)
            )

        matched.extend(
            rule_id
            for rule_id, patterns in _SPECIFIC_RULES
            if _matches_any(text, patterns)
        )
        return _result(matched)

    def check_intake(self, intake: SymptomIntake) -> RedFlagResult:
        """Apply only rules supported by explicit structured fields.

        # OPEN QUESTION: the fixed SymptomIntake contract has no fields for urine
        output/straining, seizure, collapse, ingestion subtype, gum color, blood in
        vomit, limb weight-bearing, or eye findings. It is unsafe to parse
        free_text_residual (model prose), so those rules are covered by the raw
        deterministic screen until the contract gains explicit fields.
        """
        matched: list[str] = []
        if intake.breathing_change is True:
            matched.append("breathing_difficulty")
        if BodySystem.TOXIN in intake.body_systems:
            matched.append("toxin_ingestion")
        if (
            intake.appetite_change is AppetiteChange.NOT_EATING
            and intake.duration_hours is not None
            and intake.duration_hours > 48
        ):
            matched.append("not_eating_48h")
        if (
            intake.vomiting is VomitingFrequency.REPEATED
            and intake.lethargy is True
        ):
            matched.append("vomiting_with_lethargy")
        return _result(matched)

    def check_both(self, raw_text: str, intake: SymptomIntake) -> RedFlagResult:
        """Fire when either independent path matches."""
        raw = self.check_raw(raw_text)
        structured = self.check_intake(intake)
        return _result([*raw.matched_rules, *structured.matched_rules])


def canned_response(response_id: str) -> CannedSafetyResponse:
    """Return pre-written response data by deterministic id."""
    return CANNED_RESPONSES[response_id]


def canned_response_text(response_id: str, cat_sex: CatSex) -> str:
    """Render additive sex-specific framing without ever weakening the base text."""
    response = canned_response(response_id)
    if cat_sex is CatSex.MALE and response.male_addendum is not None:
        return f"{response.text} {response.male_addendum}"
    return response.text


def _normalize_owner_text(raw_text: str) -> str:
    """Normalize mobile punctuation and trivial formatting before rule matching."""
    normalized = raw_text.translate(_APOSTROPHES).casefold()
    normalized = _BOUNDARY_PUNCTUATION.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _result(matched: list[str]) -> RedFlagResult:
    unique = list(dict.fromkeys(matched))
    if not unique:
        return RedFlagResult(
            matched_rules=[], severity=None, canned_response_id=None
        )
    unique.sort(
        key=lambda rule_id: (
            0
            if CANNED_RESPONSES[rule_id].severity is UrgencyTier.EMERGENCY
            else 1,
            rule_id,
        )
    )
    selected = CANNED_RESPONSES[unique[0]]
    return RedFlagResult(
        matched_rules=unique,
        severity=selected.severity,
        canned_response_id=selected.id,
    )
