from collections import defaultdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import AppMode
from app.core.review_scope import ActorContext, resolve_review_scope
from app.models import EvaluationRun, FraudCluster, ModelVersion, Payout, PayoutDecision, Team
from app.models.values import Decision, EvaluationStatus, HistoricalDecision
from app.repositories import risk_results as repository
from app.repositories.payout_decisions import effective_decisions
from app.risk_engine.inputs import MonitoringCoverage
from app.schemas.decisions import DecisionSource, EffectiveDecision
from app.schemas.evaluation import EvaluationError, RunFailure
from app.schemas.fraud_findings import EvidenceRef, EvidenceType, FraudFinding
from app.schemas.ml_findings import MLFinding, ValidationMetrics
from app.schemas.risk_results import (
    ClusterAssociation,
    ClusterSummary,
    ClusterView,
    Dashboard,
    EffectiveTotals,
    EvaluationQuery,
    HistoricalComparison,
    HistoricalDay,
    ModelView,
    PageQuery,
    PayoutDetail,
    PayoutPage,
    PayoutQuery,
    PayoutSummary,
    RunContext,
    RunTotals,
    RunView,
    Totals,
)
from app.services.decision_overrides import get_history, project_effective
from app.services.ingestion_service import dataset_scope


def model_view(row: ModelVersion) -> ModelView:
    return ModelView(
        model_version_id=row.model_version_id,
        algorithm=row.algorithm,
        feature_schema_version=row.feature_schema_version,
        status=row.status,
        fit_cutoff_at=row.fit_cutoff_at,
        calibration_cutoff_at=row.metrics.get("calibration_cutoff_at"),
        validation_cutoff_at=row.validation_cutoff_at,
        training_observation_count=row.training_observation_count,
        validation_observation_count=row.validation_observation_count,
        anomaly_threshold=row.anomaly_threshold,
        metrics=ValidationMetrics.model_validate(row.metrics),
        quality_gates=row.metrics.get("quality_gates", ()),
        created_at=row.created_at,
    )


def published_totals(run: EvaluationRun) -> RunTotals | None:
    if run.status != EvaluationStatus.COMPLETED or "payout_count" not in run.summary:
        return None
    return RunTotals.model_validate(run.summary)


def run_view(run: EvaluationRun, model: ModelVersion | None, mode, imported_at) -> RunView:
    failure = (run.error_summary or {}).get("code")
    return RunView(
        run_id=run.run_id,
        batch_id=run.batch_id,
        batch_mode=mode,
        batch_imported_at=imported_at,
        status=run.status,
        cutoff_at=run.cutoff_at,
        snapshot_at=run.snapshot_at,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        rule_version=run.rule_version,
        model_version_id=model.model_version_id if model else None,
        summary=published_totals(run),
        model=model_view(model) if model else None,
        failure=failure if failure in set(RunFailure) else None,
    )


def historical_comparison(frozen: dict[str, Any], current: Totals) -> HistoricalComparison:
    aggregate = {decision: Totals() for decision in HistoricalDecision}
    days = []
    for item in frozen["days"]:
        totals = None
        if item["coverage"] != MonitoringCoverage.UNKNOWN_SOURCE_COVERAGE:
            totals = {
                HistoricalDecision(row["decision"]): Totals(count=row["count"], cents=row["cents"])
                for row in item["totals"]
            }
            for decision, total in totals.items():
                aggregate[decision].count += total.count
                aggregate[decision].cents += total.cents
        days.append(
            HistoricalDay(date_ist=item["date_ist"], coverage=item["coverage"], totals=totals)
        )
    recorded = frozen["recorded_days"]
    count = sum(item.count for item in aggregate.values())
    cents = sum(item.cents for item in aggregate.values())

    def average(value: int) -> Decimal | None:
        return (Decimal(value) / recorded).quantize(Decimal(".01")) if recorded else None

    def change(value: int, historical: int) -> Decimal | None:
        if not recorded or not historical:
            return None
        return ((Decimal(value * recorded) / historical - 1) * 100).quantize(Decimal(".01"))

    return HistoricalComparison(
        **{
            key: frozen[key]
            for key in (
                "first_day",
                "last_day",
                "calendar_days",
                "recorded_days",
                "non_settlement_days",
                "unknown_days",
            )
        },
        denominator_days=recorded,
        days=tuple(days),
        totals=aggregate,
        daily_average_count=average(count),
        daily_average_cents=average(cents),
        current_vs_daily_average_count_percent=change(current.count, count),
        current_vs_daily_average_cents_percent=change(current.cents, cents),
    )


def category_for(state: EffectiveDecision) -> str:
    if state.decision == Decision.APPROVE:
        if state.decision_source == DecisionSource.HUMAN:
            return "analyst_approved"
        return "automatically_approved"
    return "open_manual_review" if state.decision == Decision.MANUAL_REVIEW else "rejected"


def payout_summary(original: PayoutDecision, payout: Payout, team: Team, state: EffectiveDecision):
    return PayoutSummary(
        payout_id=payout.payout_id,
        team_id=team.team_id,
        team_name=team.team_name,
        profession=team.profession,
        team_status=team.status,
        payout_amount_cents=payout.payout_amount_cents,
        captured_count=payout.txn_count,
        covers_from=payout.covers_from,
        covers_to=payout.covers_to,
        created_at=payout.created_at,
        original_decision=state.original_decision,
        effective_decision=state.decision,
        decision_source=state.decision_source,
        effective_version=state.effective_version,
        reason=state.reason,
        cluster_id=state.cluster_id,
        ml_status=original.ml_status,
        ml_is_anomaly=original.ml_is_anomaly,
    )


def cluster_summary(row: FraudCluster) -> ClusterSummary:
    return ClusterSummary(
        cluster_id=row.cluster_id,
        stable_case_key=row.stable_case_key,
        seed_payout_id=row.seed_payout_id,
        reason_code=row.reason_code,
        summary=row.summary,
    )


class RiskResults:
    def __init__(self, session: Session, mode: AppMode, actor: ActorContext) -> None:
        self.session = session
        self.mode = mode
        self.actor = actor
        self.dataset = dataset_scope(mode)
        self.review = resolve_review_scope(mode, actor.user_id)

    def _run_row(self, run_id: UUID, *, completed: bool = False):
        row = self.session.execute(
            repository.runs(self.dataset).where(EvaluationRun.run_id == run_id)
        ).one_or_none()
        if row is None or (completed and row[0].status != EvaluationStatus.COMPLETED):
            raise EvaluationError("evaluation_run_not_found", 404)
        return row

    def evaluations(self, filters: EvaluationQuery) -> list[RunView]:
        query = repository.runs(self.dataset)
        if filters.batch_id is not None:
            query = query.where(EvaluationRun.batch_id == filters.batch_id)
        rows = self.session.execute(
            query.order_by(EvaluationRun.created_at.desc(), EvaluationRun.run_id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return [run_view(*row) for row in rows]

    def evaluation(self, run_id: UUID) -> RunView:
        return run_view(*self._run_row(run_id))

    def models(self, filters: PageQuery) -> list[ModelView]:
        return [
            model_view(row)
            for row in repository.models(self.session, self.dataset, filters.limit, filters.offset)
        ]

    def dashboard(self, run_id: UUID) -> Dashboard:
        row = self._run_row(run_id, completed=True)
        context = run_view(*row)
        categories = EffectiveTotals(
            automatically_approved=Totals(),
            analyst_approved=Totals(),
            open_manual_review=Totals(),
            rejected=Totals(),
        )
        for original, event, name, _, cents in repository.result_amounts(
            self.session, run_id, self.dataset, self.review
        ):
            bucket = getattr(categories, category_for(project_effective(original, event, name)))
            bucket.count += 1
            bucket.cents += cents
        buckets = categories.model_dump().values()
        total = Totals(
            count=sum(bucket["count"] for bucket in buckets),
            cents=sum(bucket["cents"] for bucket in buckets),
        )
        if context.summary and (total.count, total.cents) != (
            context.summary.payout_count,
            context.summary.total_cents,
        ):
            raise EvaluationError("evaluation_totals_mismatch", 503)
        frozen = row[0].summary.get("historical_monitoring")
        return Dashboard(
            evaluation=RunContext.model_validate(context.model_dump()),
            total=total,
            categories=categories,
            historical=historical_comparison(frozen, total) if frozen else None,
            ml=context.summary.ml if context.summary else None,
            fraud_clusters=tuple(
                cluster_summary(item) for item in repository.clusters(self.session, run_id)
            ),
        )

    def payouts(self, filters: PayoutQuery) -> PayoutPage:
        self._run_row(filters.run_id, completed=True)
        rows, total = repository.payout_page(self.session, self.dataset, self.review, filters)
        return PayoutPage(
            items=tuple(
                payout_summary(original, payout, team, project_effective(original, event, name))
                for original, event, name, payout, team in rows
            ),
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    def payout(self, run_id: UUID, payout_id: str) -> PayoutDetail:
        row = repository.payout_detail(self.session, run_id, self.dataset, self.review, payout_id)
        if row is None:
            raise EvaluationError("decision_not_found", 404)
        original, _, _, payout, team = row
        audit = get_history(self.session, self.mode, self.actor, run_id, payout_id)
        data = original.findings
        return PayoutDetail(
            payout=payout_summary(original, payout, team, audit.effective),
            decisions=audit,
            transactions=repository.covered_transactions(
                self.session, run_id, payout_id, team.team_id
            ),
            statistics=data.get("statistics", ()),
            fraud=FraudFinding.model_validate(data["fraud"]) if data.get("fraud") else None,
            ml=MLFinding.model_validate(data["ml"]) if data.get("ml") else None,
        )

    def clusters(self, run_id: UUID) -> list[ClusterView]:
        self._run_row(run_id, completed=True)
        clusters = repository.clusters(self.session, run_id)
        if not clusters:
            return []
        by_key = {item.stable_case_key: item.cluster_id for item in clusters}
        case_ids = {
            ref["id"]
            for cluster in clusters
            for ref in cluster.evidence
            if ref["type"] == EvidenceType.PAYOUT
        }
        reasons = repository.historical_case_reasons(self.session, case_ids)
        associated: dict[UUID, list[ClusterAssociation]] = defaultdict(list)
        rows = self.session.execute(
            effective_decisions(run_id, self.dataset, self.review).order_by(
                PayoutDecision.payout_id
            )
        )
        for original, event, name in rows:
            state = project_effective(original, event, name)
            raw = original.findings.get("fraud")
            fraud = FraudFinding.model_validate(raw) if raw else None
            matches = (
                {
                    by_key[match.stable_case_key]: match.paths
                    for match in (fraud.primary, *fraud.alternatives)
                    if match and match.stable_case_key in by_key
                }
                if fraud
                else {}
            )
            cluster_ids = set(matches)
            for cluster_id in (original.cluster_id, state.cluster_id):
                if cluster_id is not None:
                    cluster_ids.add(cluster_id)
            for cluster_id in cluster_ids:
                associated[cluster_id].append(
                    ClusterAssociation(
                        payout_id=original.payout_id,
                        original_cluster=cluster_id == original.cluster_id,
                        effective_cluster=cluster_id == state.cluster_id,
                        paths=matches.get(cluster_id, ()),
                    )
                )
        return [
            ClusterView(
                **cluster_summary(row).model_dump(),
                reason_codes=tuple(
                    sorted(
                        {row.reason_code}
                        | {
                            reasons[ref["id"]]
                            for ref in row.evidence
                            if ref["type"] == EvidenceType.PAYOUT and ref["id"] in reasons
                        }
                    )
                ),
                evidence=tuple(EvidenceRef.model_validate(ref) for ref in row.evidence),
                associated_payouts=tuple(associated[row.cluster_id]),
            )
            for row in clusters
        ]

    def export_rows(self, run_id: UUID):
        run, *_ = self._run_row(run_id, completed=True)
        count = 0
        for original, event, name, team_id, _ in repository.result_amounts(
            self.session, run_id, self.dataset, self.review
        ):
            count += 1
            yield project_effective(original, event, name), team_id
        if count != run.summary.get("payout_count", count):
            raise EvaluationError("evaluation_totals_mismatch", 503)
