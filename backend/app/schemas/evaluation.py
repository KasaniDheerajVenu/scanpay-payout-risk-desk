from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypedDict
from uuid import UUID

from app.models.values import EvaluationStatus


class EvaluationError(ValueError):
    def __init__(self, code: str, status: int = 422, *, excluded_count: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.excluded_count = excluded_count


class RunFailure(StrEnum):
    INPUT_INVALID = "input_invalid"
    INPUT_UNAVAILABLE = "input_unavailable"
    PROCESSING_FAILED = "processing_failed"
    PUBLICATION_FAILED = "publication_failed"
    ATTEMPT_EXPIRED = "attempt_expired"


class DispositionTrace(TypedDict):
    decision: str
    decided_at: str
    import_batch_id: str
    available_at: str
    recorded_at: str
    version: int


class PayoutTrace(TypedDict):
    payout_id: str
    import_batch_id: str
    attempt_ids: list[str]


class HistoricalTrace(PayoutTrace):
    coverage_complete: bool
    eligibility: str
    disposition: DispositionTrace


class PendingExclusionTrace(TypedDict):
    payout_id: str
    code: str
    disposition: DispositionTrace


class EvaluationManifest(TypedDict):
    batch_id: str
    cutoff_at: str
    snapshot_at: str
    pending_imported_at: str
    source_input_digest: str
    configuration: dict[str, str | None]
    pending_membership_ids: list[str]
    selected_pending: list[PayoutTrace]
    pending_exclusions: list[PendingExclusionTrace]
    historical_observations: list[HistoricalTrace]
    normal_candidate_ids: list[str]
    fraud_candidate_ids: list[str]
    monitoring_payout_ids: list[str]
    team_import_batches: dict[str, str]
    attempt_import_batches: dict[str, str]


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: UUID
    batch_id: UUID
    requested_by: UUID
    status: EvaluationStatus
    cutoff_at: datetime
    snapshot_at: datetime | None
    input_digest: str | None
    rule_version: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
