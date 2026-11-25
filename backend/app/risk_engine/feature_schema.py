from decimal import Context, Decimal
from enum import StrEnum
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

FEATURE_VERSION = "payout-features-v2"
ARITHMETIC = Context(prec=40)


class Feature(StrEnum):
    PAYOUT_CENTS = "payout_cents"
    CAPTURED_COUNT = "captured_count"
    ATTEMPT_COUNT = "attempt_count"
    MEDIAN_BASE_CENTS = "median_captured_base_cents"
    MAX_BASE_CENTS = "max_captured_base_cents"
    DECLINE_RATE = "decline_rate"
    REFUND_RATE = "refund_rate"
    TIP_RATIO = "tip_ratio"
    DISTINCT_CARDS = "distinct_cards"
    REPEATED_CARD_ATTEMPTS = "repeated_card_attempts"
    CARD_ATTEMPTS = "card_attempts"
    TAP_TO_PAY_ATTEMPTS = "tap_to_pay_attempts"
    ACH_ATTEMPTS = "ach_attempts"
    DISTINCT_CARD_COUNTRIES = "distinct_card_countries"
    DOMINANT_COUNTRY_SHARE = "dominant_card_country_share"
    PEAK_ATTEMPTS = "peak_attempts_per_window"
    MEAN_BASE_CENTS = "mean_captured_base_cents"
    CAPTURED_TIPS_CENTS = "captured_tips_cents"


class Unit(StrEnum):
    CENTS = "cents"
    COUNT = "count"
    RATIO = "ratio"


class Availability(StrEnum):
    AVAILABLE = "available"
    INAPPLICABLE = "inapplicable"
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"


class Direction(StrEnum):
    HIGH = "high"
    LOW = "low"
    BOTH = "both"
    DIAGNOSTIC = "diagnostic"


class Definition(NamedTuple):
    label: str
    unit: Unit


DEFINITIONS = {
    Feature.PAYOUT_CENTS: Definition("Payable amount", Unit.CENTS),
    Feature.CAPTURED_COUNT: Definition("Captured count", Unit.COUNT),
    Feature.ATTEMPT_COUNT: Definition("Payment attempt count", Unit.COUNT),
    Feature.MEDIAN_BASE_CENTS: Definition("Median captured base amount", Unit.CENTS),
    Feature.MAX_BASE_CENTS: Definition("Largest captured base amount", Unit.CENTS),
    Feature.DECLINE_RATE: Definition("Decline rate", Unit.RATIO),
    Feature.REFUND_RATE: Definition("Refund rate", Unit.RATIO),
    Feature.TIP_RATIO: Definition("Captured tip ratio", Unit.RATIO),
    Feature.DISTINCT_CARDS: Definition("Distinct card count", Unit.COUNT),
    Feature.REPEATED_CARD_ATTEMPTS: Definition("Repeated-card attempt count", Unit.COUNT),
    Feature.CARD_ATTEMPTS: Definition("Card attempt count", Unit.COUNT),
    Feature.TAP_TO_PAY_ATTEMPTS: Definition("Tap to Pay attempt count", Unit.COUNT),
    Feature.ACH_ATTEMPTS: Definition("ACH attempt count", Unit.COUNT),
    Feature.DISTINCT_CARD_COUNTRIES: Definition("Distinct issuing-country count", Unit.COUNT),
    Feature.DOMINANT_COUNTRY_SHARE: Definition("Dominant issuing-country share", Unit.RATIO),
    Feature.PEAK_ATTEMPTS: Definition("Peak attempts in the configured window", Unit.COUNT),
    Feature.MEAN_BASE_CENTS: Definition("Average captured base amount", Unit.CENTS),
    Feature.CAPTURED_TIPS_CENTS: Definition("Total captured tips", Unit.CENTS),
}


class FeatureRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    feature: Feature
    direction: Direction
    minimum_absolute: Decimal = Field(ge=0, allow_inf_nan=False)
    minimum_relative: Decimal = Field(ge=0, allow_inf_nan=False)
    minimum_denominator: int = Field(default=1, ge=1)


def default_rules() -> tuple[FeatureRule, ...]:
    return tuple(
        FeatureRule(
            feature=feature,
            direction=direction,
            minimum_absolute=Decimal(absolute),
            minimum_relative=Decimal(relative),
            minimum_denominator=minimum,
        )
        for feature, direction, absolute, relative, minimum in (
            (Feature.PAYOUT_CENTS, Direction.BOTH, "5000", ".25", 1),
            (Feature.CAPTURED_COUNT, Direction.BOTH, "3", ".30", 1),
            (Feature.ATTEMPT_COUNT, Direction.HIGH, "4", ".35", 1),
            (Feature.MEDIAN_BASE_CENTS, Direction.BOTH, "2500", ".30", 1),
            (Feature.MAX_BASE_CENTS, Direction.HIGH, "5000", ".30", 1),
            (Feature.DECLINE_RATE, Direction.HIGH, ".15", ".50", 5),
            (Feature.REFUND_RATE, Direction.HIGH, ".10", ".50", 5),
            (Feature.TIP_RATIO, Direction.HIGH, ".15", ".50", 1000),
            (Feature.DISTINCT_CARDS, Direction.DIAGNOSTIC, "0", "0", 1),
            (Feature.REPEATED_CARD_ATTEMPTS, Direction.HIGH, "3", ".50", 1),
            (Feature.CARD_ATTEMPTS, Direction.DIAGNOSTIC, "0", "0", 1),
            (Feature.TAP_TO_PAY_ATTEMPTS, Direction.DIAGNOSTIC, "0", "0", 1),
            (Feature.ACH_ATTEMPTS, Direction.DIAGNOSTIC, "0", "0", 1),
            (Feature.DISTINCT_CARD_COUNTRIES, Direction.DIAGNOSTIC, "0", "0", 1),
            (Feature.DOMINANT_COUNTRY_SHARE, Direction.DIAGNOSTIC, "0", "0", 5),
            (Feature.PEAK_ATTEMPTS, Direction.HIGH, "3", ".50", 1),
            (Feature.MEAN_BASE_CENTS, Direction.DIAGNOSTIC, "0", "0", 1),
            (Feature.CAPTURED_TIPS_CENTS, Direction.DIAGNOSTIC, "0", "0", 1),
        )
    )


class StatisticalPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str = Field(default="statistics-v2", pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    iqr_multiplier: Decimal = Field(default=Decimal("1.5"), ge=0, allow_inf_nan=False)
    team_samples: int = Field(default=5, ge=2)
    profession_samples: int = Field(default=8, ge=2)
    peer_teams: int = Field(default=2, ge=2)
    samples_per_peer: int = Field(default=2, ge=1)
    velocity_minutes: int = Field(default=10, ge=1, le=1440)
    rules: tuple[FeatureRule, ...] = Field(default_factory=default_rules)

    @model_validator(mode="after")
    def validate_rules(self) -> "StatisticalPolicy":
        if tuple(rule.feature for rule in self.rules) != tuple(Feature):
            raise ValueError("Supply exactly one rule per feature in schema order")
        if any(
            rule.minimum_denominator != 1 and DEFINITIONS[rule.feature].unit != Unit.RATIO
            for rule in self.rules
        ):
            raise ValueError("Minimum denominators apply to ratios only")
        return self
