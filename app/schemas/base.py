"""Shared Pydantic configuration for contract models."""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base model that rejects undeclared boundary data."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

