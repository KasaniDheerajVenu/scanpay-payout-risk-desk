from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints

from app.models.values import (
    BatchMode,
    Decision,
    EvaluationStatus,
    HistoricalDecision,
    MLStatus,
    PaymentMethod,
    Profession,
    ReasonCode,
    TeamStatus,
    TransactionStatus,
)
from app.risk_engine.inputs import MonitoringCoverage
from app.schemas.decisions import DecisionHistory, DecisionSource
from app.schemas.evaluation import RunFailure
from app.schemas.fraud_findings import EvidencePath, EvidenceRef, FraudFinding
from app.schemas.ml_findings import GateResult, MLFinding, ValidationMetrics
from app.schemas.risk_findings import BaselineScope, StatisticalFinding


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    batch_id: UUID
    config_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None


class PageQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)


class EvaluationQuery(PageQuery):
    batch_id: UUID | None = None


class PayoutQuery(PageQuery):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    search: Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)] = ""
    decision: Decision | None = None
    sort: Literal["payout_id", "amount", "team_name", "created_at"] = "payout_id"
    direction: Literal["asc", "desc"] = "asc"


class Totals(BaseModel):
    count: int = 0
    cents: int = 0


class ModelView(BaseModel):
    model_version_id: UUID
    algorithm: str
    feature_schema_version: str
    status: MLStatus
    fit_cutoff_at: datetime | None
    calibration_cutoff_at: datetime | None = None
    validation_cutoff_at: datetime | None
    training_observation_count: int
    validation_observation_count: int
    anomaly_threshold: FiniteFloat | None
    metrics: ValidationMetrics
    quality_gates: tuple[GateResult, ...] = ()
    created_at: datetime


class PublishedMLMetadata(BaseModel):
    algorithm: str
    feature_schema_version: str
    status: MLStatus
    anomaly_threshold: FiniteFloat | None
    metrics: ValidationMetrics
    quality_gates: tuple[GateResult, ...] = ()


class RunML(BaseModel):
    status: MLStatus
    payout_status_counts: dict[MLStatus, int]
    metadata: PublishedMLMetadata


class FindingCounts(BaseModel):
    payouts_with_material_findings: int
    material_findings_by_feature: dict[str, int]


class RunTotals(BaseModel):
    payout_count: int
    excluded_pending_count: int
    total_cents: int
    decisions: dict[Decision, Totals]
    fraud_cluster_count: int
    statistics: FindingCounts
    ml: RunML


class RunContext(BaseModel):
    run_id: UUID
    batch_id: UUID
    batch_mode: BatchMode
    batch_imported_at: datetime | None
    status: EvaluationStatus
    cutoff_at: datetime
    snapshot_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    rule_version: str
    model_version_id: UUID | None


class RunView(RunContext):
    summary: RunTotals | None
    model: ModelView | None
    failure: RunFailure | None = None


class HistoricalDay(BaseModel):
    date_ist: date
    coverage: MonitoringCoverage
    totals: dict[HistoricalDecision, Totals] | None


class HistoricalComparison(BaseModel):
    first_day: date
    last_day: date
    calendar_days: int
    recorded_days: int
    non_settlement_days: int
    unknown_days: int
    denominator_days: int
    days: tuple[HistoricalDay, ...]
    totals: dict[HistoricalDecision, Totals]
    daily_average_count: Decimal | None
    daily_average_cents: Decimal | None
    current_vs_daily_average_count_percent: Decimal | None
    current_vs_daily_average_cents_percent: Decimal | None


class EffectiveTotals(BaseModel):
    automatically_approved: Totals
    analyst_approved: Totals
    open_manual_review: Totals
    rejected: Totals


class ClusterSummary(BaseModel):
    cluster_id: UUID
    stable_case_key: str
    seed_payout_id: str
    reason_code: ReasonCode
    summary: str


class Dashboard(BaseModel):
    evaluation: RunContext
    total: Totals
    categories: EffectiveTotals
    historical: HistoricalComparison | None
    ml: RunML | None
    fraud_clusters: tuple[ClusterSummary, ...]


class PayoutSummary(BaseModel):
    payout_id: str
    team_id: str
    team_name: str
    profession: Profession
    team_status: TeamStatus
    payout_amount_cents: int
    captured_count: int
    covers_from: date
    covers_to: date
    created_at: datetime
    original_decision: Decision
    effective_decision: Decision
    decision_source: DecisionSource
    effective_version: int
    reason: str
    cluster_id: UUID | None
    ml_status: MLStatus
    ml_is_anomaly: bool | None


class PayoutPage(BaseModel):
    items: tuple[PayoutSummary, ...]
    total: int
    limit: int
    offset: int


class CoveredTransaction(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    txn_id: str
    created_at: datetime
    collection_date_ist: date
    amount_cents: int
    tip_cents: int
    payment_method: PaymentMethod
    status: TransactionStatus


class StatisticalComparison(BaseModel):
    team: StatisticalFinding
    profession: StatisticalFinding
    preferred_scope: BaselineScope | None


class PayoutDetail(BaseModel):
    payout: PayoutSummary
    decisions: DecisionHistory
    transactions: tuple[CoveredTransaction, ...]
    statistics: tuple[StatisticalComparison, ...]
    fraud: FraudFinding | None
    ml: MLFinding | None


class ClusterAssociation(BaseModel):
    payout_id: str
    original_cluster: bool
    effective_cluster: bool
    paths: tuple[EvidencePath, ...]


class ClusterView(ClusterSummary):
    reason_codes: tuple[ReasonCode, ...]
    evidence: tuple[EvidenceRef, ...]
    associated_payouts: tuple[ClusterAssociation, ...]
