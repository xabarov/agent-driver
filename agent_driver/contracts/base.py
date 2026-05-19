"""Shared base classes for contract models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base model for public phase-0 contracts."""

    model_config = ConfigDict(extra="forbid")
