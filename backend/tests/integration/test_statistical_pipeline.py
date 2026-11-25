from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from test_evaluation import (
    FIXTURES,
    INITIAL_CUTOFF,
    add_batch,
    incremental_files,
    input_files,
)
from test_evaluation import (
    evaluation_db as evaluation_db,
)

from app.models import (
    EvaluationRun,
    FraudCluster,
    ModelVersion,
    PayoutDecision,
    PayoutDecisionHistory,
)
from app.models.values import FileType
from app.risk_engine.feature_schema import FEATURE_VERSION, Feature, StatisticalPolicy
from app.schemas.ingestion import InputFile
from app.schemas.risk_findings import BaselineScope, ComparisonStatus
from app.services.csv_source import import_options
from app.services.evaluation_input_service import capture_inputs
from app.services.evaluation_manifest import fingerprint
from app.services.ingestion_service import ingest
from app.services.statistical_analysis_service import (
    analyze_inputs,
    capture_statistical_analysis,
    evaluation_config,
)


def test_phase6_uses_database_only_snapshots_and_frozen_accepted_attempts(evaluation_db, tmp_path):
    database, settings, actor, analyst = evaluation_db
    files = {kind: tmp_path / f"{kind}.csv" for kind in FileType}
    for kind, path in files.items():
        path.write_bytes((FIXTURES / "initial" / f"{kind}.csv").read_bytes())
    with ExitStack() as stack:
        upload = {
            kind: InputFile(path.name, stack.enter_context(path.open("rb")))
            for kind, path in files.items()
        }
        batch, _ = ingest(
            database, settings, actor, upload, import_options("INITIAL", INITIAL_CUTOFF)
        )
    for path in files.values():
        path.unlink()
    assert not list(tmp_path.iterdir())

    # Store a fabricated prior publication to exercise read-only preservation.
    with database.session() as session:
        now = datetime.now(UTC)
        prior = EvaluationRun(
            batch_id=batch.batch_id,
            scope_key="local",
            requested_by=actor.user_id,
            cutoff_at=batch.cutoff_at,
            snapshot_at=now,
            completed_at=now,
            status="COMPLETED",
            input_digest="synthetic-prior",
            rule_version="prior-fixture",
            history_manifest={"fixture": "earlier publication"},
        )
        decision = PayoutDecision(
            run=prior,
            payout_id="000401",
            decision="APPROVE",
            reason="Fabricated earlier approval",
            ml_status="INSUFFICIENT_HISTORY",
            rule_version="prior-fixture",
        )
        event = PayoutDecisionHistory(
            original_decision=decision,
            scope_key="local",
            version=2,
            previous_decision="APPROVE",
            new_decision="MANUAL_REVIEW",
            change_reason="Fabricated analyst revision",
            changed_by=analyst.user_id,
        )
        session.add(event)
        session.commit()
        prior_id, decision_id, event_id = prior.run_id, decision.decision_id, event.history_id

    policy = StatisticalPolicy()
    inputs = capture_inputs(database, actor, batch.batch_id, evaluation_config(policy))
    first = analyze_inputs(inputs, policy)
    assert first.input_digest == fingerprint(inputs)
    assert first == capture_statistical_analysis(database, actor, batch.batch_id, policy)
    selected = first.pending[0]
    assert selected.observation.payout_id == "000401"
    assert selected.vector.feature_version == FEATURE_VERSION
    assert selected.observation.get(Feature.PAYOUT_CENTS).value == 3125
    assert selected.observation.get(Feature.CAPTURED_COUNT).value == 2
    assert selected.observation.get(Feature.ATTEMPT_COUNT).value == 4
    assert selected.observation.get(Feature.PAYOUT_CENTS).transaction_ids == ("000301", "000303")
    assert selected.findings and all(
        item.current_payout_id == "000401" for item in selected.findings
    )
    ids = {row.analysis.observation.payout_id for row in first.history}
    assert "000401" not in ids

    later = incremental_files()
    later[FileType.LATEST_TRANSACTIONS] += (
        b"PHASE6-LATE,0001,2025-02-17 13:00:00,10.75,0,ach,,,captured,Synthetic cardholder,00420\n"
    )
    second_batch = add_batch(evaluation_db, later)
    post_import = capture_statistical_analysis(database, actor, batch.batch_id, policy)
    assert post_import == first
    current = capture_statistical_analysis(database, actor, second_batch.batch_id, policy)
    assert [item.observation.payout_id for item in current.pending] == ["000403"]
    assert current.pending[0].observation.get(Feature.PAYOUT_CENTS).value == 4225
    different = capture_statistical_analysis(
        database, actor, batch.batch_id, StatisticalPolicy(velocity_minutes=5)
    )
    assert different.input_digest != first.input_digest

    with database.session() as session:
        assert session.get(EvaluationRun, prior_id).history_manifest == {
            "fixture": "earlier publication"
        }
        assert session.get(PayoutDecision, decision_id).decision == "APPROVE"
        assert session.get(PayoutDecisionHistory, event_id).new_decision == "MANUAL_REVIEW"
        for model, rows in (
            (EvaluationRun, 1),
            (PayoutDecision, 1),
            (PayoutDecisionHistory, 1),
            (ModelVersion, 0),
            (FraudCluster, 0),
        ):
            assert session.scalar(select(func.count()).select_from(model)) == rows


def test_postgresql_historical_approvals_produce_a_supported_low_amount_finding(evaluation_db):
    database, _, actor, _ = evaluation_db
    files = input_files(FIXTURES / "initial")
    payouts = []
    transactions = []
    for index in range(5):
        created = date(2025, 1, 8) + timedelta(weeks=index)
        start = created - timedelta(days=4)
        end = created - timedelta(days=2)
        payouts.append(
            f"PHASE6-H-{index},0001,{created} 17:30:00,{start},{end},3,120.00,"
            f"AUTO_APPROVED,Fixture system,{created} 18:00:00,,"
        )
        for offset in range(3):
            collection = start + timedelta(days=offset)
            transactions.append(
                f"PHASE6-T-{index}-{offset},0001,{collection} 10:00:00,"
                "40.00,0,ach,,,captured,Synthetic cardholder,00420"
            )
    files[FileType.HISTORY_PAYOUTS] += ("\n".join(payouts) + "\n").encode()
    files[FileType.HISTORY_TRANSACTIONS] += ("\n".join(transactions) + "\n").encode()
    batch = add_batch(evaluation_db, files, initial=True)
    result = capture_statistical_analysis(database, actor, batch.batch_id)
    finding = next(
        item
        for item in result.pending[0].findings
        if item.feature == Feature.PAYOUT_CENTS and item.baseline.scope == BaselineScope.TEAM
    )
    assert finding.status == ComparisonStatus.ANOMALY and finding.direction == "low"
    assert finding.observed == 3125 and finding.absolute_deviation == 8875
    assert finding.baseline.median == finding.lower_fence == 12000
    assert finding.baseline.iqr == 0 and finding.baseline.sample_count == 5
    assert finding.baseline.payout_ids == tuple(f"PHASE6-H-{index}" for index in range(5))
    assert len(finding.baseline.transaction_ids) == 15
    assert "$31.25" in finding.explanation and "$120.00" in finding.explanation
