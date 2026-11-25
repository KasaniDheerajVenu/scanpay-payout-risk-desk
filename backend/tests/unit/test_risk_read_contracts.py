import csv
from datetime import UTC, datetime
from io import StringIO
from uuid import uuid4

import pytest

from app.schemas.decisions import EffectiveDecision
from app.schemas.evaluation import EvaluationError
from app.schemas.fraud_findings import EvidenceRef
from app.schemas.risk_results import Totals
from app.services.decision_export import render_csv, spreadsheet_safe
from app.services.risk_results import category_for, historical_comparison


@pytest.mark.parametrize("reason", ["=1", "  +1", " -1", "\ufeff@A", "＝1", "\ttext", "\ntext"])
def test_csv_neutralizes_formula_prefixes(reason):
    assert spreadsheet_safe(reason) == "'" + reason


def test_export_escapes_reason_without_changing_ids_labels_or_evidence_order():
    cluster = uuid4()
    data = EffectiveDecision(
        decision="REJECT",
        decision_source="AUTOMATED",
        cluster_id=cluster,
        reason='Review "evidence", then check\nthe invoice.',
        evidence=(
            EvidenceRef(type="transaction", id="0002"),
            EvidenceRef(type="payout", id="0001"),
            EvidenceRef(type="team", id="0001"),
        ),
        actor=None,
        history_id=None,
        timestamp=datetime.now(UTC),
        run_id=uuid4(),
        payout_id="0003",
        decision_id=uuid4(),
        original_decision="REJECT",
        effective_version=1,
    )
    modified = data.model_copy(update={"decision": "APPROVE", "payout_id": "0004"})
    assert category_for(modified) == "automatically_approved"
    assert (
        category_for(modified.model_copy(update={"decision_source": "HUMAN"})) == "analyst_approved"
    )
    rows = list(csv.reader(StringIO(render_csv([(data, "0005"), (modified, "0005")]))))
    assert rows == [
        ["payout_id", "team_id", "decision", "fraud_cluster", "reasons", "evidence"],
        ["0003", "0005", "Reject", str(cluster), data.reason, "0001;0002"],
        ["0004", "0005", "Approve", "", data.reason, "0001;0002"],
    ]
    with pytest.raises(EvaluationError, match="invalid_export_identifier"):
        render_csv([(data, "=invalid-team")])


def test_frozen_historical_denominator_excludes_unknown_and_unscheduled_days():
    metadata = {
        "first_day": "2025-02-01",
        "last_day": "2025-02-03",
        "calendar_days": 3,
        "recorded_days": 1,
        "non_settlement_days": 1,
        "unknown_days": 1,
        "days": [
            {
                "date_ist": "2025-02-01",
                "coverage": "no_payout_scheduled",
                "totals": [
                    {"decision": "AUTO_APPROVED", "count": 0, "cents": 0},
                ],
            },
            {
                "date_ist": "2025-02-02",
                "coverage": "unknown_source_coverage",
                "totals": [
                    {"decision": "AUTO_APPROVED", "count": 0, "cents": 0},
                ],
            },
            {
                "date_ist": "2025-02-03",
                "coverage": "recorded",
                "totals": [
                    {"decision": "MANUAL_APPROVED", "count": 2, "cents": 800},
                ],
            },
        ],
    }
    result = historical_comparison(metadata, Totals(count=1, cents=1000))
    assert result.denominator_days == 1
    assert result.days[0].totals["AUTO_APPROVED"].count == 0
    assert result.days[1].totals is None
    assert result.totals["MANUAL_APPROVED"] == Totals(count=2, cents=800)
    assert result.current_vs_daily_average_count_percent == -50
    assert result.current_vs_daily_average_cents_percent == 25
    unavailable = historical_comparison(
        metadata | {"recorded_days": 0, "days": metadata["days"][:2]},
        Totals(count=1),
    )
    assert (
        unavailable.daily_average_count
        is unavailable.current_vs_daily_average_cents_percent
        is None
    )
