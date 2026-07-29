"""Typed tool contracts implemented by the Phase 2 adapters.

Operational failures are represented by ``ToolError`` on the typed output. The
The abstract bodies preserve the fixed Phase 1 interface. Concrete Phase 2
implementations return typed failures for expected retrieval, persistence, or
service errors.
"""

from abc import ABC, abstractmethod
from typing import ClassVar
from uuid import UUID

from pydantic import Field, NonNegativeFloat, PositiveInt, model_validator

from app.schemas.base import ContractModel
from app.schemas.corpora import BehaviorEntry, FunFact, HealthEntry
from app.schemas.domain import Account, CatProfile
from app.schemas.enums import (
    BehaviorCategory,
    BodySystem,
    ConfidenceLevel,
    ToolErrorCode,
    UrgencyTier,
)
from app.schemas.llm import GroundednessVerdict, SymptomIntake
from app.schemas.memory import MemoryResult


class ToolError(ContractModel):
    """Typed failure returned to orchestration so degradation remains explicit."""

    code: ToolErrorCode = Field(description="Machine-actionable failure category.")
    message: str = Field(min_length=1, description="Operator-readable failure summary.")
    retryable: bool = Field(description="Whether retrying may succeed without changes.")


class BehaviorRetrievalFilters(ContractModel):
    """Optional controlled filters over the behavior corpus."""

    category: BehaviorCategory | None = Field(
        default=None, description="Optional behavior-category filter."
    )
    confidence: ConfidenceLevel | None = Field(
        default=None, description="Optional evidence-confidence filter."
    )


class HealthRetrievalFilters(ContractModel):
    """Optional controlled filters over the veterinary corpus."""

    body_systems: list[BodySystem] = Field(
        default_factory=list, description="Optional body-system filters."
    )
    urgency_tiers: list[UrgencyTier] = Field(
        default_factory=list, description="Optional urgency filters."
    )


class BehaviorKnowledgeRetrieverInput(ContractModel):
    """Input for hybrid behavior retrieval."""

    query: str = Field(min_length=1, description="Behavior lookup query.")
    cat_id: UUID = Field(description="Mandatory active-cat request scope.")
    filters: BehaviorRetrievalFilters = Field(
        default_factory=BehaviorRetrievalFilters,
        description="Controlled behavior-corpus filters.",
    )


class VetKnowledgeRetrieverInput(ContractModel):
    """Input for retrieval-locked veterinary lookup."""

    query: str = Field(min_length=1, description="Health lookup query.")
    cat_id: UUID = Field(description="Mandatory active-cat request scope.")
    filters: HealthRetrievalFilters = Field(
        default_factory=HealthRetrievalFilters,
        description="Controlled health-corpus filters.",
    )


class RetrievalScores(ContractModel):
    """Separate scores preserve auditability across hybrid retrieval stages."""

    lexical: NonNegativeFloat = Field(description="Lexical retrieval score.")
    semantic: NonNegativeFloat = Field(description="Vector retrieval score.")
    rerank: NonNegativeFloat | None = Field(
        default=None, description="Optional cross-encoder reranker score."
    )


class RankedBehaviorEntry(ContractModel):
    """Ranked behavior entry with explicit identifiers and stage scores."""

    entry_id: str = Field(min_length=1, description="Behavior corpus entry identifier.")
    scores: RetrievalScores = Field(description="Hybrid retrieval scores.")
    entry: BehaviorEntry = Field(description="Retrieved parent behavior entry.")


class RankedHealthEntry(ContractModel):
    """Ranked health entry with explicit identifiers and stage scores."""

    entry_id: str = Field(min_length=1, description="Health corpus entry identifier.")
    scores: RetrievalScores = Field(description="Hybrid retrieval scores.")
    entry: HealthEntry = Field(description="Retrieved parent health entry.")


class BehaviorKnowledgeRetrieverOutput(ContractModel):
    """Typed behavior retrieval result or typed failure."""

    entries: list[RankedBehaviorEntry] = Field(
        default_factory=list, description="Ranked matching behavior entries."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class VetKnowledgeRetrieverOutput(ContractModel):
    """Typed health retrieval result or typed failure."""

    entries: list[RankedHealthEntry] = Field(
        default_factory=list, description="Ranked matching health entries."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class MemoryRetrieverInput(ContractModel):
    """Cat isolation enforcement point for tool-level memory lookup."""

    cat_id: UUID = Field(
        description="Non-optional isolation key applied to all profile and memory reads."
    )
    query: str = Field(min_length=1, description="Memory relevance query.")
    limit: PositiveInt = Field(
        default=5, le=50, description="Maximum summaries to return."
    )


class MemoryRetrieverOutput(ContractModel):
    """Cat-isolated memory result or typed failure."""

    result: MemoryResult | None = Field(
        default=None, description="Cat-isolated result, or null on failure."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )

    @model_validator(mode="after")
    def require_exactly_one_outcome(self) -> "MemoryRetrieverOutput":
        """Keep success and failure states unambiguous for orchestration."""
        if (self.result is None) == (self.error is None):
            raise ValueError("exactly one of result or error is required")
        return self


class RedFlagCheckerInput(ContractModel):
    """Typed symptom input for deterministic emergency-rule evaluation."""

    intake: SymptomIntake = Field(description="Structured, explicit symptom intake.")


class RedFlagResult(ContractModel):
    """Deterministic rule matches and the selected canned emergency response."""

    matched_rules: list[str] = Field(description="Identifiers of matched code rules.")
    severity: UrgencyTier | None = Field(
        description="Selected severity, or null when no rule matched."
    )
    canned_response_id: str | None = Field(
        description="Canned response identifier, or null when no rule matched."
    )

    @model_validator(mode="after")
    def match_state_is_consistent(self) -> "RedFlagResult":
        """Prevent an emergency response from existing without a matched code rule."""
        matched = bool(self.matched_rules)
        if matched != (self.severity is not None):
            raise ValueError("severity presence must match whether rules matched")
        if matched != (self.canned_response_id is not None):
            raise ValueError("canned_response_id presence must match whether rules matched")
        return self


class RedFlagCheckerOutput(ContractModel):
    """Deterministic red-flag result or typed failure."""

    result: RedFlagResult | None = Field(
        default=None, description="Deterministic result, or null on failure."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class AccountStoreInput(ContractModel):
    """Full account object for create or update persistence."""

    account: Account = Field(description="Validated account contract.")


class AccountLookupInput(ContractModel):
    """Account identifier for a get or delete operation."""

    account_id: UUID = Field(description="Account identifier.")


class AccountStoreOutput(ContractModel):
    """Account persistence result or typed failure."""

    account: Account | None = Field(
        default=None, description="Persisted or retrieved account."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class AccountDeleteOutput(ContractModel):
    """Account deletion result or typed failure."""

    deleted_account_id: UUID | None = Field(
        default=None, description="Deleted account identifier on success."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class CatProfileStoreInput(ContractModel):
    """Full cat profile for create or update persistence."""

    profile: CatProfile = Field(description="Validated cat profile contract.")


class CatProfileLookupInput(ContractModel):
    """Account-and-cat scope for a cat get or delete operation."""

    account_id: UUID = Field(description="Authenticated owning account identifier.")
    cat_id: UUID = Field(description="Mandatory cat isolation key.")


class CatProfileStoreOutput(ContractModel):
    """Cat-profile persistence result or typed failure."""

    profile: CatProfile | None = Field(
        default=None, description="Persisted or retrieved cat profile."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class CatProfileDeleteOutput(ContractModel):
    """Cat-profile deletion result or typed failure."""

    deleted_cat_id: UUID | None = Field(
        default=None, description="Deleted cat identifier on success."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class FunFactFetcherInput(ContractModel):
    """Active-cat tags and exclusions for curated fact selection."""

    cat_id: UUID = Field(description="Mandatory active-cat isolation key.")
    active_cat_tags: list[str] = Field(description="Current cat personalization tags.")
    exclude_ids: list[str] = Field(description="Fact identifiers not to return.")


class FunFactFetcherOutput(ContractModel):
    """Curated facts or typed failure."""

    facts: list[FunFact] = Field(
        default_factory=list, description="Selected curated fact cards."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class GroundingEvidence(ContractModel):
    """Retrieved evidence supplied to groundedness checking."""

    entry_id: str = Field(min_length=1, description="Retrieved corpus entry identifier.")
    text: str = Field(min_length=1, description="Retrieved text available to the draft.")


class GroundednessValidatorInput(ContractModel):
    """Draft and exact retrieved evidence used to validate support."""

    draft_answer: str = Field(min_length=1, description="Draft user-facing answer.")
    retrieved_entries: list[GroundingEvidence] = Field(
        description="Exact evidence supplied when the draft was produced."
    )


class GroundednessValidatorOutput(ContractModel):
    """Groundedness verdict or typed failure."""

    verdict: GroundednessVerdict | None = Field(
        default=None, description="Validated verdict, or null on failure."
    )
    error: ToolError | None = Field(
        default=None, description="Operational failure, or null on success."
    )


class BehaviorKnowledgeRetriever(ABC):
    """Hybrid behavior corpus retrieval; failures return ``ToolError``."""

    name: ClassVar[str] = "behavior_knowledge_retriever"

    @abstractmethod
    async def retrieve(
        self, request: BehaviorKnowledgeRetrieverInput
    ) -> BehaviorKnowledgeRetrieverOutput:
        """Return ranked behavior entries; unavailable services return typed errors."""
        raise NotImplementedError


class VetKnowledgeRetriever(ABC):
    """The only permitted source of medical facts; failures return ``ToolError``."""

    name: ClassVar[str] = "vet_knowledge_retriever"

    @abstractmethod
    async def retrieve(
        self, request: VetKnowledgeRetrieverInput
    ) -> VetKnowledgeRetrieverOutput:
        """Return ranked curated health entries or a typed retrieval failure."""
        raise NotImplementedError


class MemoryRetriever(ABC):
    """Cat-isolated profile and long-term memory retrieval; failures are typed."""

    name: ClassVar[str] = "memory_retriever"

    @abstractmethod
    async def retrieve(self, request: MemoryRetrieverInput) -> MemoryRetrieverOutput:
        """Return only facts and summaries filtered by the required cat_id."""
        raise NotImplementedError


class RedFlagChecker(ABC):
    """Pure deterministic rule evaluation with no LLM call of any kind."""

    name: ClassVar[str] = "red_flag_checker"

    @abstractmethod
    async def check(self, request: RedFlagCheckerInput) -> RedFlagCheckerOutput:
        """Return matched rules and canned response selection or a typed failure."""
        raise NotImplementedError


class ProfileStore(ABC):
    """Typed account and cat-profile CRUD; expected failures are returned, not raised."""

    name: ClassVar[str] = "profile_store"

    @abstractmethod
    async def create_account(self, request: AccountStoreInput) -> AccountStoreOutput:
        """Create an account or return a typed conflict/storage failure."""
        raise NotImplementedError

    @abstractmethod
    async def get_account(self, request: AccountLookupInput) -> AccountStoreOutput:
        """Get an account or return a typed not-found/storage failure."""
        raise NotImplementedError

    @abstractmethod
    async def update_account(self, request: AccountStoreInput) -> AccountStoreOutput:
        """Update an account or return a typed conflict/storage failure."""
        raise NotImplementedError

    @abstractmethod
    async def delete_account(self, request: AccountLookupInput) -> AccountDeleteOutput:
        """Delete an account cascade or return a typed storage failure."""
        raise NotImplementedError

    @abstractmethod
    async def create_cat(
        self, request: CatProfileStoreInput
    ) -> CatProfileStoreOutput:
        """Create a cat, returning a typed conflict when the ten-cat cap is reached."""
        raise NotImplementedError

    @abstractmethod
    async def get_cat(
        self, request: CatProfileLookupInput
    ) -> CatProfileStoreOutput:
        """Get a cat only within the supplied account-and-cat scope."""
        raise NotImplementedError

    @abstractmethod
    async def update_cat(
        self, request: CatProfileStoreInput
    ) -> CatProfileStoreOutput:
        """Update a cat or return a typed authorization/conflict failure."""
        raise NotImplementedError

    @abstractmethod
    async def delete_cat(
        self, request: CatProfileLookupInput
    ) -> CatProfileDeleteOutput:
        """Delete a cat cascade or return a typed authorization/storage failure."""
        raise NotImplementedError


class FunFactFetcher(ABC):
    """Curated fact selection by active-cat tags; failures return ``ToolError``."""

    name: ClassVar[str] = "fun_fact_fetcher"

    @abstractmethod
    async def fetch(self, request: FunFactFetcherInput) -> FunFactFetcherOutput:
        """Return pre-written facts only, or a typed retrieval failure."""
        raise NotImplementedError


class GroundednessValidator(ABC):
    """Validate draft support against exact retrieved evidence; failures are typed."""

    name: ClassVar[str] = "groundedness_validator"

    @abstractmethod
    async def validate(
        self, request: GroundednessValidatorInput
    ) -> GroundednessValidatorOutput:
        """Return a structured verdict or a typed validation-service failure."""
        raise NotImplementedError
