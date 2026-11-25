from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import Payout
from app.models.values import FileType
from app.repositories.sources import (
    captured_by_collection_date,
    collection_bounds,
    payouts_by_settlement,
)
from app.schemas.ingestion import ImportFailure
from app.services.csv_source import IST


@dataclass(frozen=True)
class PayoutWindow:
    payout: Payout
    file_type: FileType
    row: int

    @property
    def pending(self) -> bool:
        return self.file_type == FileType.PENDING_PAYOUTS

    def error(self, code: str, field: str) -> ImportFailure:
        return ImportFailure(code, file_type=self.file_type, row=self.row, field=field)


def settlement_window(created: datetime) -> tuple[date, ...]:
    local = created.astimezone(IST)
    day = local.date()
    if local.time() != time(17, 30) or day.weekday() >= 5:
        return ()
    offsets = ((4,), (4,), (4, 3, 2), (2,), (2,))[day.weekday()]
    try:
        return tuple(day - timedelta(days=offset) for offset in offsets)
    except OverflowError:
        return ()


def payable_mismatch(
    expected_count: int, expected_cents: int, count: int, cents: int
) -> tuple[str, str] | None:
    if count != expected_count:
        return "payout_count_mismatch", "txn_count"
    if cents != expected_cents:
        return "payout_amount_mismatch", "payout_amount_usd"
    return None


def within_collection_bounds(
    covers_from: date, covers_to: date, first_day: date | None, last_day: date | None
) -> bool:
    return (
        first_day is not None
        and last_day is not None
        and (first_day <= covers_from <= covers_to <= last_day)
    )


def reconcile(
    session: Session, scope: str, windows: list[PayoutWindow], cutoff: datetime
) -> list[str]:
    pending_dates = {w.payout.created_date_ist for w in windows if w.pending}
    if len(pending_dates) != 1:
        raise ImportFailure("mixed_pending_creation_dates", file_type=FileType.PENDING_PAYOUTS)

    settlements = {(w.payout.team_id, w.payout.created_date_ist) for w in windows}
    existing = payouts_by_settlement(session, scope, settlements)
    eligible_dates = {}
    for window in windows:
        payout = window.payout
        dates = settlement_window(payout.created_at)
        if not dates:
            raise window.error("invalid_settlement_time", "created_at_ist")
        if payout.covers_from not in dates or payout.covers_to not in dates:
            raise window.error("invalid_settlement_window", "covers_from")
        eligible_dates[payout.payout_id] = dates
        for other in existing[payout.team_id, payout.created_date_ist]:
            if other.payout_id == payout.payout_id:
                continue
            if payout.covers_from <= other.covers_to and other.covers_from <= payout.covers_to:
                raise window.error("overlapping_payout_coverage", "covers_from")
            raise window.error("duplicate_settlement_payout", "payout_id")

    captured = captured_by_collection_date(
        session,
        scope,
        {(w.payout.team_id, day) for w in windows for day in eligible_dates[w.payout.payout_id]},
        cutoff,
    )
    first_day, last_day = (
        collection_bounds(session, scope, cutoff)
        if any(not w.pending for w in windows)
        else (None, None)
    )
    incomplete_history: list[str] = []
    for window in windows:
        payout = window.payout
        count = cents = 0
        for day in eligible_dates[payout.payout_id]:
            day_count, day_cents = captured.get((payout.team_id, day), (0, 0))
            if payout.covers_from <= day <= payout.covers_to:
                count += day_count
                cents += day_cents
            elif window.pending and day_count:
                raise window.error("captured_outside_coverage", "covers_from")
        mismatch = payable_mismatch(payout.txn_count, payout.payout_amount_cents, count, cents)
        if window.pending:
            if mismatch is not None:
                raise window.error(*mismatch)
        elif mismatch is not None or not within_collection_bounds(
            payout.covers_from, payout.covers_to, first_day, last_day
        ):
            incomplete_history.append(payout.payout_id)
    return incomplete_history
