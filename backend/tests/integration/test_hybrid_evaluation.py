import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from json import dumps, loads
from threading import Event

import pytest
from sqlalchemy import func, select
from test_evaluation import FIXTURES, INITIAL_CUTOFF, PASSWORD, SAMPLE, add_batch, input_files
from test_evaluation import evaluation_db as evaluation_db

from app.ml.config import MLPolicy
from app.models import (
    EvaluationRun,
    FraudCluster,
    ModelVersion,
    PayoutDecision,
    PayoutDecisionHistory,
)
from app.models.values import Decision, EvaluationStatus, FileType
from app.repositories import evaluation_results
from app.risk_engine.feature_schema import StatisticalPolicy
from app.schemas.evaluation import EvaluationError
from app.schemas.fraud_findings import EvidenceRef, EvidenceType
from app.schemas.ingestion import InputFile
from app.services import evaluate_batch as executor
from app.services.csv_source import import_options
from app.services.evaluate_batch import evaluate_batch, hybrid_config
from app.services.evaluation_publication import publish_evaluation
from app.services.evaluation_service import claim_run, prepare_run
from app.services.ingestion_service import ingest
from app.services.ml_analysis_service import analyze_evaluation
from scripts import evaluate_batch as cli


def seed_fraud(context):
    files = input_files(FIXTURES / "initial")
    history = files[FileType.HISTORY_PAYOUTS].replace(b"\r\n", b"\n")
    files[FileType.HISTORY_PAYOUTS] = history.replace(
        b"Fabricated fixture case.",
        b"implicated transaction: 000102",
    )
    files[FileType.LATEST_TRANSACTIONS] = files[FileType.LATEST_TRANSACTIONS].replace(
        b"DEMO-CARD-000301",
        b"FIXTURE-CARD",
    )
    return add_batch(context, files, initial=True)


def count(session, table):
    return session.scalar(select(func.count()).select_from(table))


def test_concurrent_run_reuses_published_result_and_preserves_prior_override(
    evaluation_db,
    monkeypatch,
    caplog,
):
    database, _, admin, analyst = evaluation_db
    batch = seed_fraud(evaluation_db)
    with pytest.raises(EvaluationError, match="evaluation_forbidden"):
        evaluate_batch(database, analyst, batch.batch_id)
    started, resume = Event(), Event()
    original = executor.analyze_evaluation

    def pause(*args):
        started.set()
        assert resume.wait(15)
        return original(*args)

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
        patch.setattr(executor, "analyze_evaluation", pause)
        first = pool.submit(evaluate_batch, database, admin, batch.batch_id)
        try:
            assert started.wait(15)
            with pytest.raises(EvaluationError, match="evaluation_in_progress") as conflict:
                evaluate_batch(database, admin, batch.batch_id)
            assert conflict.value.status == 409
            with database.session() as session:
                assert count(session, PayoutDecision) == count(session, FraudCluster) == 0
        finally:
            resume.set()
        published = first.result(timeout=30)
    assert published.created and published.run.status == EvaluationStatus.COMPLETED
    replay = evaluate_batch(database, admin, batch.batch_id)
    assert not replay.created and replay.run.run_id == published.run.run_id

    with database.session() as session:
        rows = session.scalars(
            select(PayoutDecision)
            .where(PayoutDecision.run_id == published.run.run_id)
            .order_by(PayoutDecision.payout_id)
        ).all()
        assert [(row.payout_id, row.decision) for row in rows] == [
            ("000401", Decision.REJECT),
            ("000402", Decision.MANUAL_REVIEW),
        ]
        rejected, reviewed = rows
        cluster = session.get(FraudCluster, rejected.cluster_id)
        assert cluster.run_id == published.run.run_id and cluster.seed_payout_id == "000202"
        assert reviewed.cluster_id is None and reviewed.model_version_id is None
        assert {"000401", "000301", "000102", "000202"} <= {ref["id"] for ref in rejected.evidence}
        serialized = dumps(rejected.findings)
        for token in ("FIXTURE-CARD", "implicated transaction: 000102", "Fabricated cardholder"):
            assert token not in serialized
            assert token not in caplog.text
        override = PayoutDecisionHistory(
            original_decision=rejected,
            version=2,
            scope_key="local",
            previous_decision="REJECT",
            new_decision="MANUAL_REVIEW",
            changed_by=analyst.user_id,
            change_reason="Synthetic review",
        )
        session.add(override)
        session.commit()
        override_id = override.history_id
    changed = evaluate_batch(database, admin, batch.batch_id, ml_policy=MLPolicy(n_estimators=50))
    assert changed.created and changed.run.input_digest != replay.run.input_digest
    with database.session() as session:
        assert count(session, EvaluationRun) == 2 and count(session, PayoutDecision) == 4
        assert count(session, PayoutDecisionHistory) == 1
        assert (
            session.get(PayoutDecisionHistory, override_id).original_decision.run_id
            == replay.run.run_id
        )
        original_summary = session.get(EvaluationRun, replay.run.run_id).summary
        assert original_summary["payout_count"] == 2
        assert (
            original_summary["historical_monitoring"]
            == (session.get(EvaluationRun, changed.run.run_id).summary["historical_monitoring"])
        )


def test_stale_attempt_recovery_invalid_evidence_and_late_worker_fencing(
    evaluation_db, monkeypatch
):
    database, _, admin, _ = evaluation_db
    batch = seed_fraud(evaluation_db)
    config = hybrid_config(MLPolicy(), StatisticalPolicy())
    original = prepare_run(database, admin, batch.batch_id, config)
    started = claim_run(database, admin, original.run.run_id)
    with database.session() as session:
        session.get(EvaluationRun, started.run_id).started_at = datetime.now(UTC) - timedelta(
            hours=2
        )
        session.commit()

    def invalid_evidence(*args):
        result = analyze_evaluation(*args)
        first, *remaining = result.ml.pending
        bad = first.model_copy(
            update={
                "evidence": (EvidenceRef(type=EvidenceType.TRANSACTION, id="does-not-exist"),),
            }
        )
        return replace(result, ml=replace(result.ml, pending=(bad, *remaining)))

    with monkeypatch.context() as patch:
        patch.setattr(executor, "analyze_evaluation", invalid_evidence)
        with pytest.raises(EvaluationError, match="invalid_evaluation_evidence"):
            evaluate_batch(database, admin, batch.batch_id)

    def invalid_cluster(*args):
        result = analyze_evaluation(*args)
        first, *remaining = result.fraud.pending
        primary = first.primary.model_copy(update={"stable_case_key": "foreign-case"})
        bad = first.model_copy(update={"primary": primary})
        return replace(result, fraud=replace(result.fraud, pending=(bad, *remaining)))

    with monkeypatch.context() as patch:
        patch.setattr(executor, "analyze_evaluation", invalid_cluster)
        with pytest.raises(EvaluationError, match="invalid_evaluation_evidence"):
            evaluate_batch(database, admin, batch.batch_id)
    completed = evaluate_batch(database, admin, batch.batch_id)
    assert completed.created and completed.run.run_id != started.run_id
    with pytest.raises(EvaluationError, match="evaluation_run_not_running"):
        publish_evaluation(
            database, admin, started, original.inputs, analyze_evaluation(original.inputs)
        )
    with database.session() as session:
        failures = dict(
            session.execute(
                select(EvaluationRun.run_id, EvaluationRun.error_summary).where(
                    EvaluationRun.status == EvaluationStatus.FAILED
                )
            ).all()
        )
        assert failures.pop(started.run_id) == {"code": "attempt_expired"}
        assert list(failures.values()) == [{"code": "publication_failed"}] * 2
        assert count(session, PayoutDecision) == 2
        assert count(session, FraudCluster) == 1


def assessment_batch(context, path):
    database, settings, admin, _ = context
    for kind in FileType:
        payload = (SAMPLE / f"{kind}.csv").read_text(encoding="utf-8")
        if kind == FileType.HISTORY_PAYOUTS:
            payload = payload.replace(
                ",347.77,AUTO_APPROVED,System,2025-01-29 18:00:00,,",
                ",347.77,REJECTED,Fixture reviewer,2025-01-29 18:00:00,FRAUD_CARD_RING,"
                "implicated transaction: DEMO-H-09-08-0-1",
                1,
            )
        with (path / f"{kind}.csv").open("w", newline="", encoding="utf-8") as destination:
            destination.write(payload)
            writer = csv.writer(destination)
            for number in range(13, 25):
                team = f"ASSESSMENT-FIXTURE-{number}"
                if kind == FileType.TEAMS:
                    writer.writerow(
                        [
                            team,
                            f"Fictional workshop {number}",
                            "Plumbing",
                            "Fictional proprietor",
                            f"00055501{number}",
                            "Fictional address",
                            "Example Harbor",
                            "MA",
                            "2024-06-01",
                            "active",
                            "",
                            "000000019",
                            f"00{number}",
                            f"DEVICE-{number}",
                        ]
                    )
                elif number <= 20 and kind == FileType.LATEST_TRANSACTIONS:
                    writer.writerow(
                        [
                            f"{team}-TX",
                            team,
                            "2025-02-17 10:00:00",
                            "100.00",
                            "1.23",
                            "card",
                            f"SYNTHETIC-CARD-{number}",
                            "US",
                            "captured",
                            "Fictional payer",
                            "00420",
                        ]
                    )
                elif number <= 20 and kind == FileType.PENDING_PAYOUTS:
                    writer.writerow(
                        [
                            f"{team}-P",
                            team,
                            "2025-02-19 17:30:00",
                            "2025-02-15",
                            "2025-02-17",
                            1,
                            "101.23",
                            "PENDING_DECISION",
                        ]
                    )
    files = {
        kind: InputFile(f"{kind}.csv", BytesIO(data)) for kind, data in input_files(path).items()
    }
    batch, _ = ingest(
        database, settings, admin, files, import_options("INITIAL", INITIAL_CUTOFF, True)
    )
    for file in path.iterdir():
        file.unlink()
    return batch


def test_assessment_shape_atomic_rollback_and_cli_publish(
    evaluation_db, tmp_path, monkeypatch, capsys
):
    database, settings, admin, _ = evaluation_db
    batch = assessment_batch(evaluation_db, tmp_path)
    assert not list(tmp_path.iterdir())
    original = evaluation_results.store_decisions

    def duplicate(session, run, drafts, clusters, model_id):
        original(session, run, drafts, clusters, model_id)
        session.add(
            PayoutDecision(
                run_id=run.run_id,
                payout_id=drafts[0].payout_id,
                decision=Decision.APPROVE,
                reason="Synthetic duplicate",
                ml_status="INSUFFICIENT_HISTORY",
                rule_version="fixture",
            )
        )
        session.flush()

    with monkeypatch.context() as patch:
        patch.setattr(evaluation_results, "store_decisions", duplicate)
        with pytest.raises(EvaluationError, match="publication_failed") as failed:
            evaluate_batch(database, admin, batch.batch_id)
        assert failed.value.status == 503
    with database.session() as session:
        assert session.scalar(select(EvaluationRun)).status == EvaluationStatus.FAILED
        assert all(
            count(session, table) == 0 for table in (ModelVersion, FraudCluster, PayoutDecision)
        )

    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "getpass", lambda _: PASSWORD)
    args = ["--username", admin.username, "--batch-id", str(batch.batch_id)]
    assert cli.main(args) == 0
    response = loads(capsys.readouterr().out)
    assert response["status"] == "COMPLETED" and response["created"] is True
    assert cli.main(args) == 0 and loads(capsys.readouterr().out)["created"] is False
    with database.session() as session:
        run = session.scalar(select(EvaluationRun).where(EvaluationRun.status == "COMPLETED"))
        rows = session.scalars(
            select(PayoutDecision).where(PayoutDecision.run_id == run.run_id)
        ).all()
        accepted = (
            {f"DEMO-PAYOUT-{number:02}" for number in range(1, 9)}
            | {f"PEER-C-P-{number}" for number in range(9, 13)}
            | {f"ASSESSMENT-FIXTURE-{number}-P" for number in range(13, 21)}
        )
        assert len(rows) == 20 and {row.payout_id for row in rows} == accepted
        assert run.model_version_id is not None and count(session, ModelVersion) == 1
        assert count(session, FraudCluster) == 1
        assert {row.model_version_id for row in rows} == {run.model_version_id}
        for decision, totals in run.summary["decisions"].items():
            selected = [row for row in rows if row.decision == decision]
            assert totals == {
                "count": len(selected),
                "cents": sum(row.payout.payout_amount_cents for row in selected),
            }
        assert run.summary["total_cents"] == sum(row.payout.payout_amount_cents for row in rows)
        assert run.summary["total_cents"] == 247010
        original_model = run.model_version_id
        print(
            "Synthetic batch summary:",
            run.summary["decisions"],
            run.summary["total_cents"],
            run.summary["ml"]["status"],
            "insufficient-comparison reviews:",
            sum(row.reason.startswith("Comparable history is insufficient") for row in rows),
        )

    subset = add_batch(
        evaluation_db,
        {
            FileType.LATEST_TRANSACTIONS: (
                b"txn_id,team_id,created_at_ist,amount_usd,tip_usd,payment_method,card_fingerprint,"
                b"card_country,status,customer_name,customer_zip\n"
            ),
            FileType.PENDING_PAYOUTS: (
                b"payout_id,team_id,created_at_ist,covers_from,covers_to,txn_count,"
                b"payout_amount_usd,status\nASSESSMENT-FIXTURE-13-P,ASSESSMENT-FIXTURE-13,"
                b"2025-02-19 17:30:00,2025-02-15,2025-02-17,1,101.23,PENDING_DECISION\n"
            ),
        },
        cutoff=INITIAL_CUTOFF,
    )
    selected = evaluate_batch(database, admin, subset.batch_id)
    with database.session() as session:
        run = session.get(EvaluationRun, selected.run.run_id)
        assert run.model_version_id == original_model and count(session, ModelVersion) == 1
        assert [item.payout_id for item in run.decisions] == ["ASSESSMENT-FIXTURE-13-P"]
        assert run.summary["total_cents"] == 10123
