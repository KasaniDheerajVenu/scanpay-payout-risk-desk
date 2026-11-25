from contextlib import ExitStack
from datetime import UTC, datetime

from sqlalchemy import func, select
from test_evaluation import FIXTURES, INITIAL_CUTOFF, add_batch, incremental_files
from test_evaluation import evaluation_db as evaluation_db

from app.models import (
    EvaluationRun,
    FraudCluster,
    ModelVersion,
    PayoutDecision,
    PayoutDecisionHistory,
)
from app.models.values import FileType
from app.schemas.fraud_findings import Exposure, Strength
from app.schemas.ingestion import InputFile
from app.services.csv_source import import_options
from app.services.fraud_analysis_service import capture_fraud_analysis
from app.services.ingestion_service import ingest


def test_fraud_and_statistics_share_frozen_postgresql_inputs_and_leave_prior_results_intact(
    evaluation_db, tmp_path
):
    database, settings, actor, analyst = evaluation_db
    files = {kind: tmp_path / f"{kind}.csv" for kind in FileType}
    for kind, path in files.items():
        source = (FIXTURES / "initial" / f"{kind}.csv").read_bytes().replace(b"\r\n", b"\n")
        if kind == FileType.HISTORY_PAYOUTS:
            source = source.replace(
                b",MANUAL_APPROVED,Fixture Analyst,", b",REJECTED,Fixture Analyst,"
            )
            source = source.replace(
                b'OUTLIER_VERIFIED,"Fictional invoice, verified.\nSecond line."',
                b"FRAUD_CARD_TESTING,implicated transaction: 000101",
            )
            source = source.replace(b"Fabricated fixture case.", b"implicated transaction: 000102")
        if kind == FileType.LATEST_TRANSACTIONS:
            source = source.replace(b"DEMO-CARD-000301", b"FIXTURE-CARD")
        path.write_bytes(source)
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
            input_digest="synthetic-prior-fraud",
            rule_version="previous",
            history_manifest={"fixture": "previous publication"},
        )
        decision = PayoutDecision(
            run=prior,
            payout_id="000401",
            decision="APPROVE",
            reason="Synthetic earlier approval",
            ml_status="INSUFFICIENT_HISTORY",
            rule_version="previous",
        )
        event = PayoutDecisionHistory(
            original_decision=decision,
            scope_key="local",
            version=2,
            previous_decision="APPROVE",
            new_decision="MANUAL_REVIEW",
            change_reason="Synthetic analyst revision",
            changed_by=analyst.user_id,
        )
        session.add(event)
        session.commit()
        prior_id, decision_id, event_id = prior.run_id, decision.decision_id, event.history_id

    first = capture_fraud_analysis(database, actor, batch.batch_id)
    assert {item.case_payout_ids for item in first.fraud.clusters} == {("000201",), ("000202",)}
    assert first.fraud.input_digest == first.statistics.input_digest
    by_payout = {item.payout_id: item for item in first.fraud.pending}
    current = by_payout["000401"]
    assert current.status == Exposure.ESTABLISHED
    assert current.primary.strength == Strength.DIRECT
    assert current.primary.supporting_seed_ids == ("000202",)
    assert {item.id for item in current.primary.current_evidence} == {"0001", "000301", "000401"}
    assert {item.id for item in current.primary.historical_evidence} == {"0002", "000102", "000202"}
    assert "FIXTURE-CARD" not in current.model_dump_json()
    assert by_payout["000402"].status != Exposure.ESTABLISHED
    assert first.statistics.pending[0].vector.values[0] == 3125
    assert capture_fraud_analysis(database, actor, batch.batch_id) == first

    followup = incremental_files()
    followup[FileType.LATEST_TRANSACTIONS] += (
        b"PHASE7-LATE,0001,2025-02-17 13:00:00,10.75,0,card,DEMO-CARD-000101,US,captured,"
        b"Fabricated cardholder,00420\n"
    )
    add_batch(evaluation_db, followup)
    assert capture_fraud_analysis(database, actor, batch.batch_id) == first
    with database.session() as session:
        assert session.get(EvaluationRun, prior_id).history_manifest == {
            "fixture": "previous publication"
        }
        assert session.get(PayoutDecision, decision_id).decision == "APPROVE"
        assert session.get(PayoutDecisionHistory, event_id).new_decision == "MANUAL_REVIEW"
        for model, count in (
            (EvaluationRun, 1),
            (PayoutDecision, 1),
            (PayoutDecisionHistory, 1),
            (ModelVersion, 0),
            (FraudCluster, 0),
        ):
            assert session.scalar(select(func.count()).select_from(model)) == count
