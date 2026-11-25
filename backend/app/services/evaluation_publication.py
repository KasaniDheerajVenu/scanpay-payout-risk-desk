from collections import Counter
from typing import Any

from sqlalchemy import func, select

from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models import PayoutDecision
from app.models.values import Decision, EvaluationStatus, MLStatus
from app.repositories import evaluation_results
from app.repositories.evaluation_runs import lock_run
from app.risk_engine.decision_evidence import DecisionEvidence, require, source_refs
from app.risk_engine.feature_schema import Feature
from app.risk_engine.hybrid_decisions import DecisionDraft, decide
from app.risk_engine.inputs import EvaluationInputs
from app.schemas.evaluation import EvaluationError, RunSummary
from app.schemas.risk_findings import ComparisonStatus
from app.services.evaluation_input_service import evaluation_scope, reconciliation_issue
from app.services.evaluation_manifest import history_manifest, monitoring_metadata
from app.services.evaluation_service import storage, summary
from app.services.ml_analysis_service import EvaluationAnalysis


def decision_drafts(
    inputs: EvaluationInputs, analysis: EvaluationAnalysis
) -> tuple[DecisionDraft, ...]:
    rows = {row.payout.payout_id: row for row in inputs.pending}
    excluded = {row.payout_id for row in inputs.pending_exclusions}
    expected = set(inputs.batch.pending_ids)
    stats = {row.observation.payout_id: row for row in analysis.statistics.pending}
    fraud = {row.payout_id: row for row in analysis.fraud.pending}
    ml = {row.payout_id: row for row in analysis.ml.pending}
    if (
        not rows
        or rows.keys() & excluded
        or rows.keys() | excluded != expected
        or len(expected) != len(inputs.batch.pending_ids)
        or len(excluded) != len(inputs.pending_exclusions)
        or rows.keys() != stats.keys()
        or rows.keys() != fraud.keys()
        or rows.keys() != ml.keys()
        or any(
            len(rows) != len(items)
            for items in (
                inputs.pending,
                analysis.statistics.pending,
                analysis.fraud.pending,
                analysis.ml.pending,
            )
        )
    ):
        raise EvaluationError("invalid_evaluation_result_membership")

    model = analysis.ml.model
    require(model.scope_key == inputs.batch.scope_key)
    require(model.config.get("evaluation_versions") == inputs.config.version_metadata())
    evidence = DecisionEvidence(inputs, analysis.fraud.clusters)
    drafts = []
    for payout_id, row in sorted(rows.items()):
        observation, fraud_finding, ml_finding = stats[payout_id], fraud[payout_id], ml[payout_id]
        measured = observation.observation
        if (
            row.payout.team_id != measured.team_id
            or (row.payout.covers_from, row.payout.covers_to)
            != (measured.covers_from, measured.covers_to)
            or measured.get(Feature.PAYOUT_CENTS).value != row.payout.payout_amount_cents
            or measured.get(Feature.CAPTURED_COUNT).value != row.payout.txn_count
            or reconciliation_issue(row.payout, row.attempts) is not None
        ):
            raise EvaluationError("evaluation_payout_reconciliation_failed")
        require(ml_finding.training_fingerprint == model.training_fingerprint)
        require(ml_finding.feature_schema_version == model.feature_schema_version)
        require(ml_finding.anomaly_threshold == model.anomaly_threshold)
        require(ml_finding.status in (model.status, MLStatus.INFERENCE_FAILED))
        score, threshold = ml_finding.anomaly_score, ml_finding.anomaly_threshold
        require(
            ml_finding.is_anomaly
            == (score > threshold if score is not None and threshold is not None else None)
        )
        if ml_finding.qualifies_for_review:
            require(
                model.status == ml_finding.status == MLStatus.ACTIVE
                and ml_finding.is_anomaly
                and len({item.family for item in ml_finding.unusual_features})
                >= model.config["policy"]["min_actionable_families"]
            )
        evidence.check_findings(row, observation, fraud_finding, ml_finding)
        draft = decide(observation, fraud_finding, ml_finding)
        evidence.check(draft.evidence, source_refs(row) | evidence.history_refs)
        drafts.append(draft)
    return tuple(drafts)


def evaluation_summary(
    inputs: EvaluationInputs, analysis: EvaluationAnalysis, drafts: tuple[DecisionDraft, ...]
) -> dict[str, Any]:
    amounts = {row.payout.payout_id: row.payout.payout_amount_cents for row in inputs.pending}
    totals = {decision.value: {"count": 0, "cents": 0} for decision in Decision}
    statistical_flags: Counter[str] = Counter()
    model_states: Counter[str] = Counter()
    with_material_statistics = 0
    for draft in drafts:
        bucket = totals[draft.decision]
        bucket["count"] += 1
        bucket["cents"] += amounts[draft.payout_id]
        material = [
            item.feature.value
            for item in draft.statistics.findings
            if item.status == ComparisonStatus.ANOMALY
        ]
        statistical_flags.update(material)
        with_material_statistics += bool(material)
        model_states[draft.ml.status] += 1
    total_amount = sum(amounts.values())
    if (
        sum(bucket["count"] for bucket in totals.values()) != len(amounts)
        or sum(bucket["cents"] for bucket in totals.values()) != total_amount
    ):
        raise EvaluationError("evaluation_totals_mismatch")
    return {
        "payout_count": len(amounts),
        "excluded_pending_count": len(inputs.pending_exclusions),
        "total_cents": total_amount,
        "decisions": totals,
        "fraud_cluster_count": len(analysis.fraud.clusters),
        "statistics": {
            "payouts_with_material_findings": with_material_statistics,
            "material_findings_by_feature": dict(sorted(statistical_flags.items())),
        },
        "ml": {
            "status": analysis.ml.model.status,
            "payout_status_counts": dict(sorted(model_states.items())),
            "metadata": analysis.ml.model.model_dump(mode="json", exclude={"scope_key"}),
        },
        "historical_monitoring": monitoring_metadata(inputs.monitoring),
        "configuration": inputs.config.version_metadata(),
    }


def publish_evaluation(
    database: Database,
    actor: ActorContext,
    run: RunSummary,
    inputs: EvaluationInputs,
    analysis: EvaluationAnalysis,
) -> RunSummary:
    scope = evaluation_scope(database, actor)
    drafts = decision_drafts(inputs, analysis)
    result_summary = evaluation_summary(inputs, analysis, drafts)
    with storage(database) as session:
        row = lock_run(session, scope, run.run_id)
        if row is None or row.status != EvaluationStatus.RUNNING:
            raise EvaluationError("evaluation_run_not_running", 409)
        if (
            row.requested_by != actor.user_id
            or row.batch_id != inputs.batch.batch_id
            or row.cutoff_at != inputs.batch.cutoff
            or row.snapshot_at != inputs.snapshot_at
            or row.input_digest != analysis.fraud.input_digest
            or row.history_manifest != history_manifest(inputs)
        ):
            raise EvaluationError("evaluation_run_snapshot_mismatch", 409)
        model_id = evaluation_results.store_model(session, analysis.ml.model)
        clusters = evaluation_results.store_clusters(session, row, analysis.fraud.clusters)
        evaluation_results.store_decisions(session, row, drafts, clusters, model_id)
        decisions = session.execute(
            select(
                PayoutDecision.payout_id, PayoutDecision.decision, PayoutDecision.cluster_id
            ).where(PayoutDecision.run_id == row.run_id)
        ).all()
        if set(decisions) != {
            (item.payout_id, item.decision, clusters.get(item.cluster_key)) for item in drafts
        } or len(decisions) != len(drafts):
            raise EvaluationError("evaluation_persistence_mismatch")
        row.model_version_id = model_id
        row.summary = result_summary
        row.completed_at = session.scalar(select(func.clock_timestamp()))
        row.status = EvaluationStatus.COMPLETED
        session.commit()
        return summary(row)
