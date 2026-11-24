from enum import StrEnum


class Profession(StrEnum):
    PLUMBING = "Plumbing"
    LOCKSMITH = "Locksmith"
    HVAC = "HVAC"
    ELECTRICAL = "Electrical"
    GARAGE_DOOR = "Garage Door"
    APPLIANCE_REPAIR = "Appliance Repair"
    PEST_CONTROL = "Pest Control"
    HANDYMAN = "Handyman"


class TeamStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class PaymentMethod(StrEnum):
    CARD = "card"
    TAP_TO_PAY = "tap_to_pay"
    ACH = "ach"


class TransactionStatus(StrEnum):
    CAPTURED = "captured"
    DECLINED = "declined"
    REFUNDED = "refunded"


class HistoricalDecision(StrEnum):
    AUTO_APPROVED = "AUTO_APPROVED"
    MANUAL_APPROVED = "MANUAL_APPROVED"
    REJECTED = "REJECTED"


class ReasonCode(StrEnum):
    OUTLIER_VERIFIED = "OUTLIER_VERIFIED"
    VOLUME_SPIKE_VERIFIED = "VOLUME_SPIKE_VERIFIED"
    CARD_TESTING_VICTIM = "CARD_TESTING_VICTIM"
    FRAUD_CARD_RING = "FRAUD_CARD_RING"
    FRAUD_CARD_TESTING = "FRAUD_CARD_TESTING"
    FRAUD_ACCOUNT_TAKEOVER = "FRAUD_ACCOUNT_TAKEOVER"


class SourceKind(StrEnum):
    HISTORY = "HISTORY"
    PENDING = "PENDING"


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"


class BatchMode(StrEnum):
    INITIAL = "INITIAL"
    INCREMENTAL = "INCREMENTAL"


class BatchStatus(StrEnum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    FAILED = "FAILED"


class FileType(StrEnum):
    TEAMS = "teams"
    HISTORY_TRANSACTIONS = "history_transactions"
    HISTORY_PAYOUTS = "history_payouts"
    LATEST_TRANSACTIONS = "latest_transactions"
    PENDING_PAYOUTS = "pending_payouts"


class EvaluationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MLStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INFERENCE_FAILED = "INFERENCE_FAILED"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REJECT = "REJECT"


def enum_check(column: str, values: type[StrEnum]) -> str:
    choices = ", ".join(f"'{value.value}'" for value in values)
    return f"{column} IN ({choices})"
