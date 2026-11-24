import csv
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from io import StringIO
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.models.values import (
    BatchMode,
    FileType,
    HistoricalDecision,
    PaymentMethod,
    Profession,
    ReasonCode,
    TeamStatus,
    TransactionStatus,
)
from app.schemas.ingestion import ImportFailure, ImportOptions, InputFile, ParsedFile, SourceRow

IST = ZoneInfo("Asia/Kolkata")
BIGINT_MAX = 2**63 - 1
type Converter = Callable[[str], object]


def required(value: str) -> str:
    if not value:
        raise ValueError
    return value


def optional(value: str) -> str | None:
    return value or None


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError
    return value


def monetary(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,19}(?:\.[0-9]{1,2})?", value):
        raise ValueError
    cents = int(Decimal(value) * 100)
    if cents > BIGINT_MAX:
        raise ValueError
    return cents


def txn_count(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,10}", value) or int(value) > 2**31 - 1:
        raise ValueError
    return int(value)


def calendar_date(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError
    return date.fromisoformat(value)


def timestamp(value: str) -> datetime:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}"
        r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?",
        value,
    ):
        raise ValueError
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=IST)
    return instant.astimezone(UTC)


def optional_date(value: str) -> date | None:
    return calendar_date(value) if value else None


def optional_reason(value: str) -> str | None:
    return ReasonCode(value).value if value else None


def pending_status(value: str) -> str:
    if value != "PENDING_DECISION":
        raise ValueError
    return value


# Source header, destination column and converter, in the required CSV order.
TEAM_FIELDS = (
    ("team_id", "team_id", identifier),
    ("team_name", "team_name", required),
    ("profession", "profession", Profession),
    ("owner_name", "owner_name", required),
    ("owner_phone", "owner_phone", required),
    ("business_address", "business_address", required),
    ("city", "city", required),
    ("state", "state", required),
    ("onboarded_on", "onboarded_on", calendar_date),
    ("status", "status", TeamStatus),
    ("suspended_on", "suspended_on", optional_date),
    ("payout_bank_routing", "payout_bank_routing", required),
    ("payout_bank_last4", "payout_bank_last4", required),
    ("signup_device_id", "signup_device_id", optional),
)
TRANSACTION_FIELDS = (
    ("txn_id", "txn_id", identifier),
    ("team_id", "team_id", identifier),
    ("created_at_ist", "created_at", timestamp),
    ("amount_usd", "amount_cents", monetary),
    ("tip_usd", "tip_cents", monetary),
    ("payment_method", "payment_method", PaymentMethod),
    ("card_fingerprint", "card_fingerprint", optional),
    ("card_country", "card_country", optional),
    ("status", "status", TransactionStatus),
    ("customer_name", "customer_name", required),
    ("customer_zip", "customer_zip", required),
)
PAYOUT_FIELDS = (
    ("payout_id", "payout_id", identifier),
    ("team_id", "team_id", identifier),
    ("created_at_ist", "created_at", timestamp),
    ("covers_from", "covers_from", calendar_date),
    ("covers_to", "covers_to", calendar_date),
    ("txn_count", "txn_count", txn_count),
    ("payout_amount_usd", "payout_amount_cents", monetary),
)
SCHEMAS: dict[FileType, tuple[tuple[str, str, Converter], ...]] = {
    FileType.TEAMS: TEAM_FIELDS,
    FileType.HISTORY_TRANSACTIONS: TRANSACTION_FIELDS,
    FileType.LATEST_TRANSACTIONS: TRANSACTION_FIELDS,
    FileType.HISTORY_PAYOUTS: PAYOUT_FIELDS
    + (
        ("decision", "historical_decision", HistoricalDecision),
        ("decided_by", "historical_decided_by", optional),
        ("decided_at_ist", "historical_decided_at", timestamp),
        ("reason_code", "historical_reason_code", optional_reason),
        ("reason", "historical_reason", optional),
    ),
    FileType.PENDING_PAYOUTS: PAYOUT_FIELDS + (("status", "source_status", pending_status),),
}


def import_options(mode: str, cutoff: str, assessment: bool = False) -> ImportOptions:
    try:
        batch_mode = BatchMode(mode)
    except ValueError:
        raise ImportFailure("invalid_mode", field="mode") from None
    try:
        cutoff_at = timestamp(cutoff)
        cutoff_at.astimezone(IST)
    except (ValueError, OverflowError):
        raise ImportFailure("invalid_cutoff", field="cutoff") from None
    if assessment and batch_mode != BatchMode.INITIAL:
        raise ImportFailure("invalid_assessment_mode", field="assessment")
    return ImportOptions(batch_mode, cutoff_at, assessment)


def sanitized_filename(filename: str) -> str:
    basename = PurePosixPath(filename.replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", basename).lstrip(".-")[:128]
    return cleaned or "upload.csv"


def parse_source(
    kind: FileType, source: InputFile, options: ImportOptions, settings: Settings, budget: int
) -> ParsedFile:
    byte_limit = min(settings.upload_max_file_bytes, budget)
    data = source.stream.read(byte_limit + 1)
    if len(data) > byte_limit:
        raise ImportFailure("upload_too_large", status=413, file_type=kind)
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeError:
        raise ImportFailure("invalid_encoding", file_type=kind) from None
    if any(ord(char) < 32 and char not in "\r\n\t" for char in decoded):
        raise ImportFailure("invalid_csv", file_type=kind)
    fields = SCHEMAS[kind]
    reader = csv.reader(StringIO(decoded, newline=""), strict=True)
    rows: list[SourceRow] = []
    line = 1
    try:
        if next(reader, None) != [header for header, _, _ in fields]:
            raise ImportFailure("invalid_header", file_type=kind, row=line)
        while True:
            line = reader.line_num + 1
            record = next(reader, None)
            if record is None:
                break
            if len(rows) >= settings.upload_max_rows_per_file:
                raise ImportFailure("row_limit_exceeded", status=413, file_type=kind, row=line)
            if len(record) != len(fields):
                raise ImportFailure("invalid_column_count", file_type=kind, row=line)
            values = {}
            for (header, column, convert), value in zip(fields, record, strict=True):
                if len(value) > settings.upload_max_field_chars:
                    raise ImportFailure(
                        "field_limit_exceeded", status=413, file_type=kind, row=line, field=header
                    )
                try:
                    values[column] = convert(value.strip())
                except (ValueError, OverflowError):
                    raise ImportFailure(
                        "invalid_value", file_type=kind, row=line, field=header
                    ) from None
            normalize_dates(kind, values, options, line)
            rows.append(SourceRow(line, values))
    except csv.Error:
        raise ImportFailure("invalid_csv", file_type=kind, row=line) from None
    return ParsedFile(
        kind, sanitized_filename(source.filename), sha256(data).hexdigest(), len(data), rows
    )


def normalize_dates(kind: FileType, values: dict, options: ImportOptions, line: int) -> None:
    def invalid(code: str, field: str) -> ImportFailure:
        return ImportFailure(code, file_type=kind, row=line, field=field)

    if kind == FileType.TEAMS:
        onboarded, suspended = values["onboarded_on"], values["suspended_on"]
        cutoff_date = options.cutoff.astimezone(IST).date()
        if onboarded > cutoff_date:
            raise invalid("after_cutoff", "onboarded_on")
        if suspended is not None and not onboarded <= suspended <= cutoff_date:
            raise invalid("invalid_suspension", "suspended_on")
        return
    created = values["created_at"]
    if created > options.cutoff:
        raise invalid("after_cutoff", "created_at_ist")
    try:
        local_date = created.astimezone(IST).date()
    except OverflowError:
        raise invalid("invalid_value", "created_at_ist") from None
    if kind in {FileType.HISTORY_TRANSACTIONS, FileType.LATEST_TRANSACTIONS}:
        values["collection_date_ist"] = local_date
        if values["payment_method"] == PaymentMethod.ACH and values["card_fingerprint"] is not None:
            raise invalid("invalid_ach_fingerprint", "card_fingerprint")
        if values["amount_cents"] + values["tip_cents"] > BIGINT_MAX:
            raise invalid("monetary_total_out_of_range", "tip_usd")
        return
    values["created_date_ist"] = local_date
    if not values["covers_from"] <= values["covers_to"] < local_date:
        raise invalid("invalid_coverage", "covers_to")
    decided = values.get("historical_decided_at")
    if decided is not None and not created <= decided <= options.cutoff:
        raise invalid("invalid_disposition_timestamp", "decided_at_ist")
