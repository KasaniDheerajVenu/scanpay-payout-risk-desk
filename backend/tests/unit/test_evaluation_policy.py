from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.models.values import HistoricalDecision, ReasonCode
from app.risk_engine.eligibility import historical_eligibility, is_fraud_seed, monitoring_input
from app.risk_engine.inputs import Disposition, Eligibility, MonitoringCoverage

CUTOFF = datetime(2026, 11, 13, 4, 30, tzinfo=UTC)
SNAPSHOT = datetime(2027, 1, 1, tzinfo=UTC)
APPROVED = Disposition(
    HistoricalDecision.MANUAL_APPROVED,
    CUTOFF,
    "Synthetic actor",
    None,
    None,
    UUID(int=1),
    SNAPSHOT,
    SNAPSHOT,
    1,
)


@pytest.mark.parametrize(
    ("reason", "result"),
    [
        (None, Eligibility.NORMAL_CANDIDATE),
        (ReasonCode.OUTLIER_VERIFIED, Eligibility.NORMAL_CANDIDATE),
        (ReasonCode.VOLUME_SPIKE_VERIFIED, Eligibility.NORMAL_CANDIDATE),
        (ReasonCode.CARD_TESTING_VICTIM, Eligibility.NORMAL_CANDIDATE),
        (ReasonCode.FRAUD_CARD_TESTING, Eligibility.FRAUD_INDICATOR),
        (ReasonCode.FRAUD_CARD_RING, Eligibility.FRAUD_INDICATOR),
        (ReasonCode.FRAUD_ACCOUNT_TAKEOVER, Eligibility.FRAUD_INDICATOR),
    ],
)
def test_historical_policy_keeps_verified_context_and_separates_fraud(reason, result):
    disposition = replace(APPROVED, reason_code=reason)
    assert historical_eligibility(disposition, CUTOFF, SNAPSHOT, True) == result
    assert not is_fraud_seed(disposition)
    rejected = replace(disposition, decision=HistoricalDecision.REJECTED)
    assert is_fraud_seed(rejected) == (result == Eligibility.FRAUD_INDICATOR)


def test_historical_policy_requires_both_clocks_and_complete_coverage():
    assert historical_eligibility(None, CUTOFF, SNAPSHOT, True) == Eligibility.UNVERIFIED
    after_cutoff = replace(APPROVED, decided_at=CUTOFF + timedelta(microseconds=1))
    assert historical_eligibility(after_cutoff, CUTOFF, SNAPSHOT, True) == Eligibility.AFTER_CUTOFF
    unavailable = replace(APPROVED, available_at=SNAPSHOT + timedelta(microseconds=1))
    assert historical_eligibility(unavailable, CUTOFF, SNAPSHOT, True) == Eligibility.UNAVAILABLE
    assert (
        historical_eligibility(APPROVED, CUTOFF, SNAPSHOT, False) == Eligibility.INCOMPLETE_COVERAGE
    )
    rejected = replace(APPROVED, decision=HistoricalDecision.REJECTED)
    assert historical_eligibility(rejected, CUTOFF, SNAPSHOT, True) == Eligibility.NOT_APPROVED


def test_monitoring_window_matches_the_assessment_and_identifies_unknown_weekdays():
    history = monitoring_input(date(2026, 11, 12), ())
    assert (history.first_day, history.last_day) == (date(2026, 10, 23), date(2026, 11, 11))
    assert history.calendar_days == len(history.days) == 20
    assert history.recorded_days == 0
    assert history.non_settlement_days + history.unknown_days == 20
    assert history.days[0].coverage == MonitoringCoverage.UNKNOWN_SOURCE_COVERAGE
    assert history.days[1].coverage == MonitoringCoverage.NO_PAYOUT_SCHEDULED
