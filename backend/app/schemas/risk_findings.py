from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.risk_engine.feature_schema import Availability, Direction, Feature, Unit
from app.risk_engine.features import Coverage


class BaselineScope(StrEnum):
    TEAM = "TEAM"
    PROFESSION = "PROFESSION"


class Reliability(StrEnum):
    AVAILABLE = "available"
    NO_COMPARABLE_VALUES = "no_comparable_values"
    TOO_FEW_OBSERVATIONS = "too_few_observations"
    TOO_FEW_PEERS = "too_few_peers"


class Baseline(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: BaselineScope
    reliability: Reliability
    coverage: Coverage
    sample_count: int
    peer_team_count: int | None = None
    excluded_peer_count: int = 0
    prefilter_sample_count: int
    distribution_count: int
    period_from: date | None = None
    period_to: date | None = None
    median: Decimal | None = None
    q1: Decimal | None = None
    q3: Decimal | None = None
    iqr: Decimal | None = None
    payout_ids: tuple[str, ...] = ()
    transaction_ids: tuple[str, ...] = ()


class ComparisonStatus(StrEnum):
    ANOMALY = "anomaly"
    NONMATERIAL = "nonmaterial"
    IN_RANGE = "in_range"
    INSUFFICIENT_HISTORY = "insufficient_history"
    OBSERVATION_UNAVAILABLE = "observation_unavailable"
    DIAGNOSTIC = "diagnostic"


class StatisticalFinding(BaseModel):
    model_config = ConfigDict(frozen=True)
    feature: Feature
    feature_version: str
    policy_version: str
    unit: Unit
    observed: Decimal | int | None
    observation_availability: Availability
    observed_denominator: int | None
    baseline: Baseline
    status: ComparisonStatus
    direction: Direction | None = None
    configured_direction: Direction
    lower_fence: Decimal | None = None
    upper_fence: Decimal | None = None
    iqr_multiplier: Decimal
    minimum_absolute: Decimal
    minimum_relative: Decimal
    minimum_denominator: int
    absolute_deviation: Decimal | None = None
    relative_deviation: Decimal | None = None
    is_material: bool | None = None
    current_payout_id: str
    current_transaction_ids: tuple[str, ...]
    explanation: str
