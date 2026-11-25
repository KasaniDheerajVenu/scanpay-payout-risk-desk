from datetime import UTC, datetime

from sqlalchemy import func, select
from test_evaluation import SAMPLE, add_batch, input_files
from test_evaluation import evaluation_db as evaluation_db

from app.models import (
    EvaluationRun,
    FraudCluster,
    ModelVersion,
    Payout,
    PayoutDecision,
    PayoutDecisionHistory,
)
from app.models.values import FileType, MLStatus
from app.services.ml_analysis_service import capture_ml_analysis
from app.services.statistical_analysis_service import HistoryUse


def test_database_only_model_is_refitted_and_preserves_prior_results(evaluation_db, tmp_path):
    database, _, admin, analyst = evaluation_db
    for kind in FileType:
        source = (SAMPLE / f"{kind}.csv").read_bytes()
        if kind == FileType.HISTORY_PAYOUTS:
            approved = b",347.77,AUTO_APPROVED,System,2025-01-29 18:00:00,,"
            assert approved in source
            source = source.replace(
                approved,
                b",347.77,REJECTED,Fixture reviewer,2025-01-29 18:00:00,FRAUD_CARD_RING,"
                b"implicated transaction: DEMO-H-09-08-0-1",
                1,
            )
        if kind == FileType.HISTORY_TRANSACTIONS:
            source = source.replace(b"DEMO-CARD-DEMO-H-15-01-0-0", b"DEMO-CARD-DEMO-H-09-08-0-1")
        (tmp_path / f"{kind}.csv").write_bytes(source)
    batch = add_batch(evaluation_db, input_files(tmp_path), initial=True)
    for path in tmp_path.iterdir():
        path.unlink()
    assert not list(tmp_path.iterdir())

    with database.session() as session:
        payout_id = session.scalar(
            select(Payout.payout_id)
            .where(Payout.source_status == "PENDING_DECISION")
            .order_by(Payout.payout_id)
        )
        now = datetime.now(UTC)
        run = EvaluationRun(
            batch_id=batch.batch_id,
            scope_key="local",
            requested_by=admin.user_id,
            cutoff_at=batch.cutoff_at,
            snapshot_at=now,
            completed_at=now,
            status="COMPLETED",
            input_digest="phase8-prior-synthetic",
            rule_version="prior",
            history_manifest={"publication": "fixture"},
        )
        decision = PayoutDecision(
            run=run,
            payout_id=payout_id,
            decision="APPROVE",
            reason="Synthetic earlier approval",
            ml_status="INSUFFICIENT_HISTORY",
            rule_version="prior",
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
        ids = run.run_id, decision.decision_id, event.history_id

    result = capture_ml_analysis(database, admin, batch.batch_id)
    assert result.fraud.clusters
    seed_ids = {item.seed_payout_id for item in result.fraud.clusters}
    assert all(
        row.use == HistoryUse.EXCLUDED
        for row in result.statistics.history
        if row.analysis.observation.payout_id in seed_ids
    )
    assert "DEMO-H-P-15-01" in {item.exclusion.payout_id for item in result.fraud.contamination}
    contaminated = next(
        row
        for row in result.statistics.history
        if row.analysis.observation.payout_id == "DEMO-H-P-15-01"
    )
    assert contaminated.linked_exclusion and contaminated.use == HistoryUse.EXCLUDED
    model = result.ml.model
    assert model.status in {MLStatus.ACTIVE, MLStatus.DIAGNOSTIC_ONLY}
    assert model.metrics.training_fitted >= 30
    assert model.metrics.validation_scored >= 15
    assert model.anomaly_threshold is not None
    assert len(result.ml.pending) == 12
    assert all(item.anomaly_score is not None for item in result.ml.pending)
    assert all(item.payout_id == item.evidence[0].id for item in result.ml.pending)
    assert all("DEMO-CARD" not in item.model_dump_json() for item in result.ml.pending)
    assert result.ml == capture_ml_analysis(database, admin, batch.batch_id).ml
    assert model.persistence_fields().keys() <= set(ModelVersion.__table__.columns.keys())
    print(model.status, model.metrics.model_dump_json(), "threshold", model.anomaly_threshold)
    print("validation gates:", [(item.code, item.passed) for item in model.quality_gates])

    with database.session() as session:
        assert session.get(EvaluationRun, ids[0]).history_manifest == {"publication": "fixture"}
        assert session.get(PayoutDecision, ids[1]).decision == "APPROVE"
        assert session.get(PayoutDecisionHistory, ids[2]).new_decision == "MANUAL_REVIEW"
        for table, count in (
            (EvaluationRun, 1),
            (PayoutDecision, 1),
            (PayoutDecisionHistory, 1),
            (ModelVersion, 0),
            (FraudCluster, 0),
        ):
            assert session.scalar(select(func.count()).select_from(table)) == count
