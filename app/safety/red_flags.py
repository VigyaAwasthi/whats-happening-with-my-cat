"""Pure coded red-flag rules. This module has no model dependency."""

import re
from dataclasses import dataclass

from app.schemas.enums import (
    AppetiteChange,
    BodySystem,
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


CANNED_RESPONSES: dict[str, CannedSafetyResponse] = {
    "urinary_obstruction": CannedSafetyResponse(
        id="urinary_obstruction",
        severity=UrgencyTier.EMERGENCY,
        text=(
            "Straining to urinate with little or no output can be life-threatening. "
            "Please seek emergency veterinary care immediately."
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


_RAW_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "urinary_obstruction",
        (
            r"\b(can'?t|cannot|unable to)\s+(pee|urinate)\b",
            r"\b(no|little)\s+urine\b",
            r"\bstraining\s+to\s+(pee|urinate)\b",
            r"\bstraining\b.*\blitter\s+box\b.*\b(nothing|no output)\b",
            r"\bkeeps?\s+(going|returning)\s+to\s+the\s+litter\s+box\b.*\b(pee|urine|nothing)\b",
        ),
    ),
    (
        "breathing_difficulty",
        (
            r"\b(not|isn'?t)\s+breathing\b",
            r"\b(difficulty|trouble)\s+breathing\b",
            r"\b(open[- ]mouth|labou?red)\s+breathing\b",
            r"\bbreathing\b.*\bmouth\s+open\b",
            r"\bgasping\b",
        ),
    ),
    ("seizure", (r"\bseiz(ure|ing|ed)\b",)),
    (
        "collapse",
        (r"\bcollaps(e|ed|ing)\b", r"\bunresponsive\b", r"\bwon'?t\s+wake\b"),
    ),
    (
        "human_medication",
        (
            r"\b(swallowed|ate|eaten|licked)\b.*\b(pill|tablet|capsule|medication|medicine)\b",
            r"\b(ibuprofen|acetaminophen|paracetamol|tylenol|advil)\b",
        ),
    ),
    (
        "lily_exposure",
        (
            r"\b(ate|eaten|chewed|licked|drank|touched|exposed)\b.*\blil(y|ies)\b",
            r"\blil(y|ies)\b.*\b(pollen|vase water|petal|leaf)\b",
        ),
    ),
    (
        "toxin_ingestion",
        (
            r"\b(ate|eaten|swallowed|licked|drank|got into|exposed)\b.*\b(toxin|poison|antifreeze|rodenticide)\b",
            r"\btoxin\s+ingestion\b",
        ),
    ),
    (
        "abnormal_gums",
        (
            r"\b(blue|grey|gray|very pale|white)\s+gums?\b",
            r"\bgums?\b.*\b(blue|grey|gray|very pale|white)\b",
        ),
    ),
    (
        "not_eating_48h",
        (
            r"\b(not eaten|hasn'?t eaten|no food)\b.*\b(48\s*hours?|2\s*days?|two\s*days?)\b",
            r"\b(not eaten|hasn'?t eaten|no food)\b.*\b([3-9]|[1-9][0-9]+)\s*days?\b",
            r"\b(48\s*hours?|2\s*days?|two\s*days?)\b.*\b(not eaten|without food)\b",
        ),
    ),
    (
        "vomiting_blood",
        (
            r"\b(vomit(ed|ing)?|threw up|throwing up)\b.*\bblood\b",
            r"\bblood\b.*\b(vomit|vomited|vomiting|throw up)\b",
        ),
    ),
    (
        "vomiting_with_lethargy",
        (
            r"\b(repeated|keeps?|multiple times)\b.*\bvomit(ed|ing)?\b.*\b(lethargic|lethargy|very tired)\b",
            r"\bvomit(ed|ing)?\b.*\b(lethargic|lethargy)\b",
            r"\b(lethargic|lethargy|very tired)\b.*\b(repeated|keeps?|multiple times)\b.*\bvomit(ed|ing)?\b",
        ),
    ),
    (
        "cannot_bear_weight",
        (
            r"\b(can'?t|cannot|unable to|won'?t)\s+(bear|put)\s+weight\b",
            r"\bnot\s+using\s+(a|the)\s+(leg|limb)\b",
        ),
    ),
    (
        "painful_eye",
        (
            r"\b(cloudy|bulging)\s+eye\b",
            r"\beye\b.*\b(cloudy|bulging)\b",
            r"\beye\b.*\b(clearly painful|held shut|severe pain)\b",
        ),
    ),
)


class DeterministicRedFlagChecker(RedFlagChecker):
    """Coded raw-text and structured-intake rules; no LLM call is possible."""

    # OPEN QUESTION: CatProfile has no sex field, so the specified male-cat
    # severity refinement cannot be based on profile data and is not inferred
    # from pronouns. The urinary obstruction rule still escalates every cat.

    async def check(self, request: RedFlagCheckerInput) -> RedFlagCheckerOutput:
        return RedFlagCheckerOutput(result=self.check_intake(request.intake))

    def check_raw(self, raw_text: str) -> RedFlagResult:
        """Cheap first-pass screen over owner text."""
        matched = [
            rule_id
            for rule_id, patterns in _RAW_RULES
            if any(re.search(pattern, raw_text, flags=re.IGNORECASE) for pattern in patterns)
        ]
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
