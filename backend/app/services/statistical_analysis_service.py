from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models.values import ReasonCode
from app.risk_engine.baselines import build_baseline
from app.risk_engine.eligibility import historical_eligibility
from app.risk_engine.feature_schema import (
    ARITHMETIC,
    FEATURE_VERSION,
    Availability,
    Direction,
    Feature,
    StatisticalPolicy,
)
from app.risk_engine.features import FeatureObservation, Number, extract_features
from app.risk_engine.inputs import (
    CollectionAvailability,
    Eligibility,
    EvaluationConfig,
    EvaluationInputs,
    HistoricalObservation,
)
from app.risk_engine.statistical_anomalies import compare
from app.schemas.risk_findings import (
    BaselineScope,
    ComparisonStatus,
    Reliability,
    StatisticalFinding,
)
from app.services.evaluation_input_service import capture_inputs, reconciliation_issue
from app.services.evaluation_manifest import canonical_json, fingerprint
from app.services.reconciliation_service import within_collection_bounds

VALIDATION_REASONS = frozenset(
    {
        ReasonCode.OUTLIER_VERIFIED,
        ReasonCode.VOLUME_SPIKE_VERIFIED,
        ReasonCode.CARD_TESTING_VICTIM,
    }
)
MODEL_FEATURE_NAMES = tuple(
    name
    for feature in Feature
    for name in (
        feature.value,
        f"{feature.value}_relative_team",
        f"{feature.value}_relative_profession",
    )
)


class HistoryUse(StrEnum):
    FIT_CANDIDATE = "fit_candidate"
    VALIDATION_ONLY = "validation_only"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class FraudExclusion:
    payout_id: str
    known_at: datetime
    available_at: datetime


@dataclass(frozen=True, slots=True)
class ModelVector:
    """Ordered, numerical inputs only. IDs, labels and evidence are held by the caller."""

    feature_version: str
    names: tuple[str, ...]
    values: tuple[Number | None, ...]
    availability: tuple[Availability, ...]


@dataclass(frozen=True, slots=True)
class FeatureComparison:
    team: StatisticalFinding
    profession: StatisticalFinding
    preferred_scope: BaselineScope | None

    @property
    def findings(self) -> tuple[StatisticalFinding, ...]:
        anomalies = tuple(
            item for item in (self.team, self.profession) if item.status == ComparisonStatus.ANOMALY
        )
        if (
            self.preferred_scope is None
            and self.team.configured_direction != Direction.DIAGNOSTIC
            and self.team.status == ComparisonStatus.INSUFFICIENT_HISTORY
        ):
            return (self.team, self.profession)
        return anomalies


@dataclass(frozen=True, slots=True)
class ObservationAnalysis:
    observation: FeatureObservation
    comparisons: tuple[FeatureComparison, ...]
    vector: ModelVector

    @property
    def findings(self) -> tuple[StatisticalFinding, ...]:
        return tuple(item for pair in self.comparisons for item in pair.findings)


@dataclass(frozen=True, slots=True)
class HistoricalFeatureRow:
    analysis: ObservationAnalysis
    use: HistoryUse
    eligibility: Eligibility
    reason_code: ReasonCode | None
    linked_exclusion: bool
    feature_available_by: datetime
    label_decided_at: datetime
    label_available_at: datetime


@dataclass(frozen=True, slots=True)
class StatisticalAnalysis:
    input_digest: str
    policy_version: str
    pending: tuple[ObservationAnalysis, ...]
    history: tuple[HistoricalFeatureRow, ...]


def evaluation_config(
    policy: StatisticalPolicy, base: EvaluationConfig | None = None
) -> EvaluationConfig:
    base = base or EvaluationConfig()
    payload = canonical_json(policy.model_dump(mode="json"))
    return base.model_copy(
        update={
            "feature_version": FEATURE_VERSION,
            "statistics_digest": sha256(payload.encode()).hexdigest(),
        }
    )


def is_linked(
    exclusions: tuple[FraudExclusion, ...], payout_id: str, cutoff: datetime, available: datetime
) -> bool:
    return any(
        item.payout_id == payout_id and item.known_at <= cutoff and item.available_at <= available
        for item in exclusions
    )


def project_history(
    source: HistoricalObservation,
    days: tuple[CollectionAvailability, ...],
    available: datetime,
    policy: StatisticalPolicy,
) -> tuple[FeatureObservation, bool]:
    payout = source.payout
    attempts = tuple(
        row
        for row in source.attempts
        if row.provenance.imported_at <= available and row.created_at < payout.created_at
    )
    observed_dates = [
        item.collection_date
        for item in days
        if item.first_available_at <= available and item.collection_date < payout.created_date
    ]
    complete = (
        bool(attempts)
        and reconciliation_issue(payout, attempts) is None
        and within_collection_bounds(
            payout.covers_from,
            payout.covers_to,
            min(observed_dates, default=None),
            max(observed_dates, default=None),
        )
    )
    return extract_features(source, policy, attempts=attempts, complete=complete), complete


def relative_value(finding: StatisticalFinding) -> tuple[Number | None, Availability]:
    if finding.observed is None:
        return None, finding.observation_availability
    center = finding.baseline.median
    if center is None:
        return None, Availability.UNAVAILABLE
    if center == 0:
        return None, Availability.INAPPLICABLE
    with localcontext(ARITHMETIC):
        return (Decimal(finding.observed) - center) / abs(center), Availability.AVAILABLE


def analyze_observation(
    subject: FeatureObservation, cohort: tuple[FeatureObservation, ...], policy: StatisticalPolicy
) -> ObservationAnalysis:
    comparisons = []
    values: list[Number | None] = []
    availability = []
    for rule in policy.rules:
        item = subject.get(rule.feature)
        team, profession = (
            compare(
                subject, build_baseline(subject, cohort, rule.feature, scope, policy), rule, policy
            )
            for scope in BaselineScope
        )
        preferred = next(
            (
                entry.baseline.scope
                for entry in (team, profession)
                if entry.baseline.reliability == Reliability.AVAILABLE
            ),
            None,
        )
        comparisons.append(FeatureComparison(team, profession, preferred))
        team_relative, team_state = relative_value(team)
        profession_relative, profession_state = relative_value(profession)
        values.extend((item.value, team_relative, profession_relative))
        availability.extend((item.availability, team_state, profession_state))
    return ObservationAnalysis(
        subject,
        tuple(comparisons),
        ModelVector(FEATURE_VERSION, MODEL_FEATURE_NAMES, tuple(values), tuple(availability)),
    )


def analyze_inputs(
    inputs: EvaluationInputs,
    policy: StatisticalPolicy | None = None,
    *,
    exclusions: tuple[FraudExclusion, ...] = (),
) -> StatisticalAnalysis:
    policy = policy or StatisticalPolicy()
    config = evaluation_config(policy, inputs.config)
    if inputs.config.feature_version not in (None, FEATURE_VERSION) or (
        inputs.config.statistics_digest is not None and inputs.config != config
    ):
        raise ValueError("statistical_policy_fingerprint_mismatch")

    days_by_team: dict[str, list[CollectionAvailability]] = defaultdict(list)
    for item in inputs.historical_collection_days:
        days_by_team[item.team_id].append(item)
    sources = tuple(
        sorted(inputs.history, key=lambda row: (row.payout.created_at, row.payout.payout_id))
    )
    projections: dict[tuple[str, datetime], tuple[FeatureObservation, bool]] = {}

    def projection(source: HistoricalObservation, available: datetime):
        key = (source.payout.payout_id, available)
        if key not in projections:
            projections[key] = project_history(
                source, tuple(days_by_team[source.payout.team_id]), available, policy
            )
        return projections[key]

    def cohort(cutoff: datetime, available: datetime, before: datetime):
        rows = []
        for source in sources:
            payout = source.payout
            if payout.created_at >= before:
                break
            if (
                source.disposition.reason_code in VALIDATION_REASONS
                or historical_eligibility(source.disposition, cutoff, available, True)
                != Eligibility.NORMAL_CANDIDATE
                or is_linked(exclusions, payout.payout_id, cutoff, available)
            ):
                continue
            observed, complete = projection(source, available)
            if complete:
                rows.append(observed)
        return tuple(rows)

    past = []
    for row in sources:
        payout, label = row.payout, row.disposition
        as_of = min(payout.provenance.imported_at, inputs.snapshot_at)
        observed, complete = projection(row, as_of)
        eligibility = historical_eligibility(
            label, inputs.batch.cutoff, inputs.snapshot_at, complete
        )
        linked = is_linked(exclusions, payout.payout_id, inputs.batch.cutoff, inputs.snapshot_at)
        use = HistoryUse.EXCLUDED
        if eligibility == Eligibility.NORMAL_CANDIDATE and not linked:
            use = (
                HistoryUse.VALIDATION_ONLY
                if label.reason_code in VALIDATION_REASONS
                else HistoryUse.FIT_CANDIDATE
            )
        earlier = cohort(payout.created_at, as_of, payout.created_at)
        past.append(
            HistoricalFeatureRow(
                analyze_observation(observed, earlier, policy),
                use,
                eligibility,
                label.reason_code,
                linked,
                as_of,
                label.decided_at,
                label.available_at,
            )
        )

    pending = tuple(
        analyze_observation(
            extract_features(row, policy),
            cohort(inputs.batch.cutoff, inputs.snapshot_at, row.payout.created_at),
            policy,
        )
        for row in sorted(inputs.pending, key=lambda item: item.payout.payout_id)
    )
    # The default path shares the Phase 5 fingerprint. Time-qualified graph exclusions can
    # contribute to derived statistical identity when a later phase supplies them.
    digest = fingerprint(replace(inputs, config=config))
    if exclusions:
        records = sorted(
            {(item.payout_id, item.known_at, item.available_at) for item in exclusions}
        )
        digest = sha256(canonical_json([digest, records]).encode()).hexdigest()
    return StatisticalAnalysis(digest, policy.version, pending, tuple(past))


def capture_statistical_analysis(
    database: Database, actor: ActorContext, batch_id: UUID, policy: StatisticalPolicy | None = None
) -> StatisticalAnalysis:
    policy = policy or StatisticalPolicy()
    inputs = capture_inputs(database, actor, batch_id, evaluation_config(policy))
    return analyze_inputs(inputs, policy)
