from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.values import Decision
from app.schemas.fraud_findings import EvidenceRef

SOURCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class DecisionProblem(ValueError):
    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class RequestedEvidence(EvidenceRef):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: Annotated[str, StringConstraints(pattern=SOURCE_ID_PATTERN)]


class OverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    decision: Decision
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    expected_version: Annotated[int, Field(strict=True, ge=1, lt=2**31 - 1)]
    evidence: tuple[RequestedEvidence, ...] = Field(default=(), max_length=100)
    cluster_id: UUID | None = None


class DecisionSource(StrEnum):
    AUTOMATED = "AUTOMATED"
    HUMAN = "HUMAN"


class AuditStatus(StrEnum):
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"


class DecisionActor(BaseModel):
    user_id: UUID
    display_name: str


class DecisionVersion(BaseModel):
    decision: Decision
    decision_source: DecisionSource
    cluster_id: UUID | None
    reason: str
    evidence: tuple[EvidenceRef, ...]
    actor: DecisionActor | None
    history_id: UUID | None
    timestamp: datetime


class EffectiveDecision(DecisionVersion):
    decision: Decision = Field(serialization_alias="effective_decision")
    run_id: UUID
    payout_id: str
    decision_id: UUID
    original_decision: Decision
    effective_version: int


class DecisionEvent(DecisionVersion):
    decision: Decision = Field(serialization_alias="new_decision")
    version: int
    previous_decision: Decision | None
    status: AuditStatus


class DecisionHistory(BaseModel):
    effective: EffectiveDecision
    history: tuple[DecisionEvent, ...]
