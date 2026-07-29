"""Narrow task adapters over the generic structured model client."""

from app.llm.client import ModelPurpose, StructuredLLMClient
from app.prompts.v1 import MEMORY_SUMMARY_SYSTEM_PROMPT_V1
from app.schemas.llm import MemorySummary


class FastMemorySummarizer:
    """MemorySummarizer implementation using the configured fast model."""

    def __init__(self, client: StructuredLLMClient, model: str) -> None:
        self._client = client
        self._model = model

    async def summarize(self, messages: list[str]) -> MemorySummary | None:
        result = await self._client.generate(
            MemorySummary,
            model=self._model,
            purpose=ModelPurpose.FAST,
            system_prompt=MEMORY_SUMMARY_SYSTEM_PROMPT_V1,
            cache_context="",
            user_prompt="\n".join(messages),
            max_tokens=600,
        )
        return result.value

