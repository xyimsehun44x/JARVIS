from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"


class ProposedAction(BaseModel):
    tool_name: str
    risk: RiskLevel
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, exclude=True)


class PermissionPolicy:
    def __init__(self, *, confirm_reversible: bool = False) -> None:
        self.confirm_reversible = confirm_reversible

    def requires_confirmation(self, action: ProposedAction) -> bool:
        if action.risk in {RiskLevel.EXTERNAL_WRITE, RiskLevel.DESTRUCTIVE}:
            return True
        return action.risk is RiskLevel.REVERSIBLE and self.confirm_reversible
