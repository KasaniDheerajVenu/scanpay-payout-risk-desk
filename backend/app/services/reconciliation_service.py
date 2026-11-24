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
            if (
                other.payout_id != payout.payout_id
                and payout.covers_from <= other.covers_to
                and other.covers_from <= payout.covers_to
            ):
                raise window.error("overlapping_payout_coverage", "covers_from")

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
        if window.pending:
            if count != payout.txn_count:
                raise window.error("payout_count_mismatch", "txn_count")
            if cents != payout.payout_amount_cents:
                raise window.error("payout_amount_mismatch", "payout_amount_usd")
        elif (
            first_day is None
            or last_day is None
            or not first_day <= payout.covers_from <= payout.covers_to <= last_day
            or count != payout.txn_count
            or cents != payout.payout_amount_cents
        ):
            incomplete_history.append(payout.payout_id)
    return incomplete_history
