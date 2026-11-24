from datetime import date, datetime, timedelta

from app.models.values import HistoricalDecision, ReasonCode
from app.risk_engine.inputs import (
    Disposition,
    DispositionTotals,
    Eligibility,
    HistoricalObservation,
    MonitoringCoverage,
    MonitoringDay,
    MonitoringInput,
)

FRAUD_CODES = frozenset(
    {ReasonCode.FRAUD_CARD_RING, ReasonCode.FRAUD_CARD_TESTING, ReasonCode.FRAUD_ACCOUNT_TAKEOVER}
)


def historical_eligibility(
    disposition: Disposition | None, cutoff: datetime, snapshot_at: datetime, complete: bool
) -> Eligibility:
    if disposition is None:
        return Eligibility.UNVERIFIED
    if disposition.decided_at > cutoff:
        return Eligibility.AFTER_CUTOFF
    if disposition.available_at > snapshot_at:
        return Eligibility.UNAVAILABLE
    if disposition.reason_code in FRAUD_CODES:
        return Eligibility.FRAUD_INDICATOR
    if disposition.decision not in {
        HistoricalDecision.AUTO_APPROVED,
        HistoricalDecision.MANUAL_APPROVED,
    }:
        return Eligibility.NOT_APPROVED
    return Eligibility.NORMAL_CANDIDATE if complete else Eligibility.INCOMPLETE_COVERAGE


def is_fraud_seed(disposition: Disposition) -> bool:
    return (
        disposition.decision == HistoricalDecision.REJECTED
        and disposition.reason_code in FRAUD_CODES
    )


def monitoring_input(
    current_date: date, history: tuple[HistoricalObservation, ...]
) -> MonitoringInput:
    first_day = current_date - timedelta(days=20)
    grouped: dict[tuple[date, HistoricalDecision], list[HistoricalObservation]] = {}
    for row in history:
        if first_day <= row.payout.created_date < current_date:
            grouped.setdefault((row.payout.created_date, row.disposition.decision), []).append(row)
    days: list[MonitoringDay] = []
    for offset in range(20):
        day = first_day + timedelta(days=offset)
        totals = []
        for decision in HistoricalDecision:
            rows = grouped.get((day, decision), [])
            totals.append(
                DispositionTotals(
                    decision,
                    len(rows),
                    sum(row.payout.payout_amount_cents for row in rows),
                    tuple(sorted(row.payout.payout_id for row in rows)),
                )
            )
        if any(item.count for item in totals):
            coverage = MonitoringCoverage.RECORDED
        elif day.weekday() >= 5:
            coverage = MonitoringCoverage.NO_PAYOUT_SCHEDULED
        else:
            coverage = MonitoringCoverage.UNKNOWN_SOURCE_COVERAGE
        days.append(MonitoringDay(day, coverage, tuple(totals)))
    return MonitoringInput(
        first_day,
        days[-1].date_ist,
        len(days),
        sum(day.coverage == MonitoringCoverage.RECORDED for day in days),
        sum(day.coverage == MonitoringCoverage.NO_PAYOUT_SCHEDULED for day in days),
        sum(day.coverage == MonitoringCoverage.UNKNOWN_SOURCE_COVERAGE for day in days),
        tuple(days),
    )
