"""Deterministic citation validation followed by a structured fast-model judge."""

from collections.abc import Sequence

from app.llm.client import ModelPurpose, StructuredLLMClient
from app.prompts.v1 import GROUNDEDNESS_SYSTEM_PROMPT_V1
from app.schemas.enums import ToolErrorCode
from app.schemas.llm import GroundednessVerdict, TriageResult
from app.tools.contracts import (
    GroundingEvidence,
    GroundednessValidator,
    GroundednessValidatorInput,
    GroundednessValidatorOutput,
    ToolError,
)


class CompositeGroundednessValidator(GroundednessValidator):
    """Cheap citation gate plus schema-constrained semantic support judge."""

    def __init__(self, client: StructuredLLMClient, fast_model: str) -> None:
        self._client = client
        self._fast_model = fast_model

    def validate_citations(
        self, draft: TriageResult, retrieved_entry_ids: set[str]
    ) -> GroundednessVerdict:
        """Reject fabricated ids before spending a model call."""
        unsupported = [
            claim.text
            for claim in draft.claims
            if claim.source_entry_id not in retrieved_entry_ids
        ]
        return GroundednessVerdict(
            passed=not unsupported,
            unsupported_claims=unsupported,
            notes=(
                "all claim ids are in the retrieved set"
                if not unsupported
                else "one or more claim ids were not retrieved"
            ),
        )

    async def validate(
        self, request: GroundednessValidatorInput
    ) -> GroundednessValidatorOutput:
        if not request.retrieved_entries:
            return GroundednessValidatorOutput(
                verdict=GroundednessVerdict(
                    passed=False,
                    unsupported_claims=[request.draft_answer],
                    notes="no retrieved evidence was supplied",
                )
            )
        context = "\n\n".join(
            f"ENTRY_ID: {entry.entry_id}\n{entry.text}"
            for entry in request.retrieved_entries
        )
        result = await self._client.generate(
            GroundednessVerdict,
            model=self._fast_model,
            purpose=ModelPurpose.FAST,
            system_prompt=GROUNDEDNESS_SYSTEM_PROMPT_V1,
            cache_context=context,
            user_prompt=request.draft_answer,
            max_tokens=700,
        )
        if result.value is not None:
            return GroundednessValidatorOutput(verdict=result.value)
        return GroundednessValidatorOutput(
            error=result.error
            or ToolError(
                code=ToolErrorCode.UNAVAILABLE,
                message="groundedness judge unavailable",
                retryable=False,
            )
        )

    async def validate_health(
        self,
        draft: TriageResult,
        retrieved_entry_ids: set[str],
        evidence: Sequence[tuple[str, str]],
    ) -> GroundednessVerdict:
        """Run both required health stages and fail closed on judge error."""
        deterministic = self.validate_citations(draft, retrieved_entry_ids)
        if not deterministic.passed:
            return deterministic
        output = await self.validate(
            GroundednessValidatorInput(
                draft_answer=(
                    draft.message
                    + "\n"
                    + "\n".join(claim.text for claim in draft.claims)
                ),
                retrieved_entries=[
                    GroundingEvidence(entry_id=entry_id, text=text)
                    for entry_id, text in evidence
                ],
            )
        )
        if output.verdict is not None:
            return output.verdict
        return GroundednessVerdict(
            passed=False,
            unsupported_claims=[claim.text for claim in draft.claims],
            notes="groundedness judge failed closed",
        )
