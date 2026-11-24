from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.values import FileType
from app.schemas.ingestion import ImportFailure, InputFile
from app.services.csv_source import SCHEMAS, import_options, parse_source
from app.services.reconciliation_service import settlement_window

INITIAL = Path(__file__).resolve().parents[1] / "fixtures" / "initial"
OPTIONS = import_options("INITIAL", "2025-02-20 10:00")
SETTINGS = Settings()


def parse(kind: FileType, data: bytes):
    return parse_source(kind, InputFile("file.csv", BytesIO(data)), OPTIONS, SETTINGS, 10**6)


@pytest.mark.parametrize("kind", list(FileType))
def test_normalizes_all_source_columns(kind: FileType) -> None:
    parsed = parse(kind, b"\xef\xbb\xbf" + (INITIAL / f"{kind}.csv").read_bytes())
    first = parsed.rows[0].values
    expected = {column for _, column, _ in SCHEMAS[kind]}
    derived = {"created_date_ist", "collection_date_ist"} & first.keys()
    assert first.keys() == expected | derived
    assert first["team_id"] == "0001"
    if kind == FileType.TEAMS:
        assert first["payout_bank_routing"] == "000000019"
        assert first["payout_bank_last4"] == "0001"
        assert first["owner_phone"] == "0005550001"
        assert first["suspended_on"] is None
    if kind == FileType.LATEST_TRANSACTIONS:
        assert first["created_at"] == datetime(2025, 2, 15, 5, 30, tzinfo=UTC)
        assert first["collection_date_ist"] == date(2025, 2, 15)
        assert first["amount_cents"] == 1000
        assert first["tip_cents"] == 100
        assert first["customer_zip"] == "00420"
        assert parsed.rows[-1].values["card_fingerprint"] is None
    if kind == FileType.HISTORY_PAYOUTS:
        assert first["historical_reason"] == "Fictional invoice, verified.\nSecond line."
        assert parsed.rows[1].line == 4
        assert parsed.rows[1].values["historical_decided_at"] == datetime(
            2025, 2, 12, 12, 45, tzinfo=UTC
        )


def test_nullable_suspension_dates_preserve_historical_context() -> None:
    data = (INITIAL / "teams.csv").read_bytes()
    active_with_history = data.replace(b",active,,", b",active,2025-02-14,", 1)
    assert parse(FileType.TEAMS, active_with_history).rows[0].values["suspended_on"] == date(
        2025, 2, 14
    )
    invalid = data.replace(b",suspended,2025-02-14,", b",suspended,2024-01-01,", 1)
    with pytest.raises(ImportFailure, match="invalid_suspension"):
        parse(FileType.TEAMS, invalid)


@pytest.mark.parametrize(
    ("original", "replacement", "issue", "field"),
    [
        (b"txn_id,", b"missing,", "invalid_header", None),
        (b"txn_id,", b"txn_id,txn_id,", "invalid_header", None),
        (b"txn_id,", b"txn_id,unexpected,", "invalid_header", None),
        (b"10.00", b"10.001", "invalid_value", "amount_usd"),
        (b"10.00", b"92233720368547758.08", "invalid_value", "amount_usd"),
        (b"10.00", b"NaN", "invalid_value", "amount_usd"),
        (b"10.00", b"-1", "invalid_value", "amount_usd"),
        (b"000301,", b"=FORMULA,", "invalid_value", "txn_id"),
        (b"11:00:00", b"xx:00:00", "invalid_value", "created_at_ist"),
        (b"captured,", b"posted,", "invalid_value", "status"),
        (
            b",ach,,,captured,",
            b",ach,fingerprint,,captured,",
            "invalid_ach_fingerprint",
            "card_fingerprint",
        ),
        (b"00420", b'"00420', "invalid_csv", None),
        (b"00420", b"\xff", "invalid_encoding", None),
        (b"00420", b"00420\x00", "invalid_csv", None),
        (b"2025-02-15", b"2025-02-25", "after_cutoff", "created_at_ist"),
    ],
)
def test_rejects_malformed_values_without_echo(
    original: bytes, replacement: bytes, issue: str, field: str | None
) -> None:
    data = (INITIAL / "latest_transactions.csv").read_bytes()
    assert original in data
    with pytest.raises(ImportFailure) as raised:
        parse(FileType.LATEST_TRANSACTIONS, data.replace(original, replacement, 1))
    assert raised.value.details["code"] == issue
    assert raised.value.details.get("field") == field
    assert replacement.decode("utf-8", errors="replace") not in str(raised.value.details)


@pytest.mark.parametrize(
    ("created", "expected"),
    [
        ("2025-02-17 17:30", (13,)),
        ("2025-02-18 17:30", (14,)),
        ("2025-02-19 17:30", (15, 16, 17)),
        ("2025-02-20 17:30", (18,)),
        ("2025-02-21 17:30", (19,)),
        ("2025-02-22 17:30", ()),
        ("2025-02-19 16:30", ()),
    ],
)
def test_settlement_schedule(created: str, expected: tuple[int, ...]) -> None:
    assert (
        tuple(day.day for day in settlement_window(import_options("INITIAL", created).cutoff))
        == expected
    )
