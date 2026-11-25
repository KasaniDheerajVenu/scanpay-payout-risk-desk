from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.values import (
    BatchMode,
    HistoricalDecision,
    PaymentMethod,
    Profession,
    ReasonCode,
    SourceKind,
    TeamStatus,
    TransactionStatus,
)


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rule_version: str = Field(default="input-v1", pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    feature_version: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    statistics_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    fraud_policy_version: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    fraud_mapping_version: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    fraud_mapping_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    ml_policy_version: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    ml_policy_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    def version_metadata(self) -> dict[str, str | None]:
        versions = {"rule_version": self.rule_version, "feature_version": self.feature_version}
        if self.statistics_digest is not None:
            versions["statistics_digest"] = self.statistics_digest
        if self.fraud_policy_version is not None:
            versions["fraud_policy_version"] = self.fraud_policy_version
        if self.fraud_mapping_version is not None:
            versions["fraud_mapping_version"] = self.fraud_mapping_version
        if self.fraud_mapping_digest is not None:
            versions["fraud_mapping_digest"] = self.fraud_mapping_digest
        if self.ml_policy_version is not None:
            versions["ml_policy_version"] = self.ml_policy_version
        if self.ml_policy_digest is not None:
            versions["ml_policy_digest"] = self.ml_policy_digest
        return versions


@dataclass(frozen=True, slots=True)
class Provenance:
    import_batch_id: UUID
    imported_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class SelectedBatch:
    batch_id: UUID
    scope_key: str
    mode: BatchMode
    cutoff: datetime
    imported_at: datetime
    input_digest: str
    payout_date: date
    pending_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class TeamInput:
    team_id: str
    team_name: str
    profession: Profession
    owner_name: str
    owner_phone: str
    business_address: str
    city: str
    state: str
    onboarded_on: date
    status: TeamStatus
    suspended_on: date | None
    payout_bank_routing: str
    payout_bank_last4: str
    signup_device_id: str | None
    provenance: Provenance


@dataclass(frozen=True, slots=True, repr=False)
class Attempt:
    txn_id: str
    team_id: str
    created_at: datetime
    collection_date: date
    amount_cents: int
    tip_cents: int
    payment_method: PaymentMethod
    card_fingerprint: str | None
    card_country: str | None
    status: TransactionStatus
    customer_name: str
    customer_zip: str
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class PayoutInput:
    payout_id: str
    team_id: str
    created_at: datetime
    created_date: date
    covers_from: date
    covers_to: date
    txn_count: int
    payout_amount_cents: int
    source_kind: SourceKind
    provenance: Provenance

    @property
    def coverage_days(self) -> int:
        return (self.covers_to - self.covers_from).days + 1


@dataclass(frozen=True, slots=True, repr=False)
class Disposition:
    decision: HistoricalDecision
    decided_at: datetime
    decided_by: str | None
    reason_code: ReasonCode | None
    reason: str | None
    import_batch_id: UUID
    available_at: datetime
    recorded_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class PendingObservation:
    payout: PayoutInput
    profession: Profession
    attempts: tuple[Attempt, ...]


@dataclass(frozen=True, slots=True)
class PendingExclusion:
    payout_id: str
    disposition: Disposition


class Eligibility(StrEnum):
    NORMAL_CANDIDATE = "normal_candidate"
    UNVERIFIED = "unverified"
    AFTER_CUTOFF = "after_cutoff"
    UNAVAILABLE = "unavailable"
    NOT_APPROVED = "not_approved"
    FRAUD_INDICATOR = "fraud_indicator"
    INCOMPLETE_COVERAGE = "incomplete_coverage"


@dataclass(frozen=True, slots=True)
class HistoricalObservation:
    payout: PayoutInput
    profession: Profession
    attempts: tuple[Attempt, ...]
    disposition: Disposition
    coverage_complete: bool
    eligibility: Eligibility


class MonitoringCoverage(StrEnum):
    RECORDED = "recorded"
    NO_PAYOUT_SCHEDULED = "no_payout_scheduled"
    UNKNOWN_SOURCE_COVERAGE = "unknown_source_coverage"


@dataclass(frozen=True, slots=True)
class DispositionTotals:
    decision: HistoricalDecision
    count: int
    cents: int
    payout_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MonitoringDay:
    date_ist: date
    coverage: MonitoringCoverage
    totals: tuple[DispositionTotals, ...]


@dataclass(frozen=True, slots=True)
class MonitoringInput:
    first_day: date
    last_day: date
    calendar_days: int
    recorded_days: int
    non_settlement_days: int
    unknown_days: int
    days: tuple[MonitoringDay, ...]


@dataclass(frozen=True, slots=True)
class CollectionAvailability:
    team_id: str
    collection_date: date
    first_available_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class EvaluationInputs:
    batch: SelectedBatch
    snapshot_at: datetime
    config: EvaluationConfig
    teams: tuple[TeamInput, ...]
    pending: tuple[PendingObservation, ...]
    pending_exclusions: tuple[PendingExclusion, ...]
    history: tuple[HistoricalObservation, ...]
    fraud_candidates: tuple[HistoricalObservation, ...]
    monitoring: MonitoringInput
    historical_collection_days: tuple[CollectionAvailability, ...] = ()

    @property
    def normal_candidates(self) -> tuple[HistoricalObservation, ...]:
        return tuple(row for row in self.history if row.eligibility == Eligibility.NORMAL_CANDIDATE)
