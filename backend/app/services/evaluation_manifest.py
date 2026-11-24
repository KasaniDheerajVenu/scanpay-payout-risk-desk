import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from app.risk_engine.inputs import (
    Disposition,
    EvaluationInputs,
    HistoricalObservation,
    MonitoringInput,
    PendingObservation,
)
from app.schemas.evaluation import DispositionTrace, EvaluationManifest, PayoutTrace


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def json_value(value: Any) -> str:
    if isinstance(value, datetime):
        return utc_iso(value)
    if isinstance(value, date | UUID):
        return str(value)
    raise TypeError("Unsupported evaluation metadata type")


def canonical_json(value: object) -> str:
    return json.dumps(
        value, default=json_value, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def monitoring_metadata(monitoring: MonitoringInput) -> dict[str, Any]:
    data = asdict(monitoring)
    data["days"] = []
    for day in sorted(monitoring.days, key=lambda row: row.date_ist):
        data["days"].append(
            {
                "date_ist": day.date_ist,
                "coverage": day.coverage,
                "totals": [
                    {**asdict(total), "payout_ids": sorted(total.payout_ids)}
                    for total in sorted(day.totals, key=lambda row: row.decision)
                ],
            }
        )
    return json.loads(canonical_json(data))


def fingerprint(inputs: EvaluationInputs) -> str:
    def observation(row: HistoricalObservation | PendingObservation) -> dict:
        return asdict(
            replace(row, attempts=tuple(sorted(row.attempts, key=lambda item: item.txn_id)))
        )

    payload = {
        "batch": {**asdict(inputs.batch), "pending_ids": sorted(inputs.batch.pending_ids)},
        "config": inputs.config.model_dump(),
        "teams": [asdict(row) for row in sorted(inputs.teams, key=lambda row: row.team_id)],
        "pending": [
            observation(row) for row in sorted(inputs.pending, key=lambda row: row.payout.payout_id)
        ],
        "pending_exclusions": [
            asdict(row) for row in sorted(inputs.pending_exclusions, key=lambda row: row.payout_id)
        ],
        "history": [
            observation(row) for row in sorted(inputs.history, key=lambda row: row.payout.payout_id)
        ],
        "fraud_candidates": sorted(row.payout.payout_id for row in inputs.fraud_candidates),
        "monitoring": monitoring_metadata(inputs.monitoring),
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def disposition_metadata(disposition: Disposition) -> DispositionTrace:
    return {
        "decision": disposition.decision,
        "decided_at": utc_iso(disposition.decided_at),
        "import_batch_id": str(disposition.import_batch_id),
        "available_at": utc_iso(disposition.available_at),
        "recorded_at": utc_iso(disposition.recorded_at),
        "version": disposition.version,
    }


def history_manifest(inputs: EvaluationInputs) -> EvaluationManifest:
    def record(row: HistoricalObservation | PendingObservation) -> PayoutTrace:
        return {
            "payout_id": row.payout.payout_id,
            "import_batch_id": str(row.payout.provenance.import_batch_id),
            "attempt_ids": sorted(item.txn_id for item in row.attempts),
        }

    return {
        "batch_id": str(inputs.batch.batch_id),
        "cutoff_at": utc_iso(inputs.batch.cutoff),
        "snapshot_at": utc_iso(inputs.snapshot_at),
        "pending_imported_at": utc_iso(inputs.batch.imported_at),
        "source_input_digest": inputs.batch.input_digest,
        "configuration": {
            "rule_version": inputs.config.rule_version,
            "feature_version": inputs.config.feature_version,
        },
        "pending_membership_ids": sorted(inputs.batch.pending_ids),
        "selected_pending": [
            record(row) for row in sorted(inputs.pending, key=lambda item: item.payout.payout_id)
        ],
        "pending_exclusions": [
            {
                "payout_id": row.payout_id,
                "code": "verified_disposition_known",
                "disposition": disposition_metadata(row.disposition),
            }
            for row in sorted(inputs.pending_exclusions, key=lambda item: item.payout_id)
        ],
        "historical_observations": [
            {
                **record(row),
                "coverage_complete": row.coverage_complete,
                "eligibility": row.eligibility,
                "disposition": disposition_metadata(row.disposition),
            }
            for row in sorted(inputs.history, key=lambda item: item.payout.payout_id)
        ],
        "normal_candidate_ids": sorted(row.payout.payout_id for row in inputs.normal_candidates),
        "fraud_candidate_ids": sorted(row.payout.payout_id for row in inputs.fraud_candidates),
        "monitoring_payout_ids": sorted(
            payout_id
            for day in inputs.monitoring.days
            for item in day.totals
            for payout_id in item.payout_ids
        ),
        "team_import_batches": {
            row.team_id: str(row.provenance.import_batch_id) for row in inputs.teams
        },
        "attempt_import_batches": {
            attempt.txn_id: str(attempt.provenance.import_batch_id)
            for row in (*inputs.pending, *inputs.history)
            for attempt in row.attempts
        },
    }
