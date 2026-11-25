from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import (
    CurrentUser,
    get_database,
    get_session,
    require_admin,
    require_permission,
)
from app.api.payout_decisions import PayoutId
from app.core.permissions import Permission
from app.core.review_scope import ActorContext
from app.risk_engine.hybrid_decisions import RULE_VERSION
from app.schemas.evaluation import EvaluationError
from app.schemas.risk_results import (
    ClusterView,
    Dashboard,
    EvaluationQuery,
    EvaluationRequest,
    ModelView,
    PageQuery,
    PayoutDetail,
    PayoutPage,
    PayoutQuery,
    RunView,
)
from app.services.decision_export import render_csv
from app.services.evaluate_batch import evaluate_batch
from app.services.risk_results import RiskResults

router = APIRouter(prefix="/api", tags=["risk operations"])
require_read = require_permission(Permission.READ_RISK)
require_export = require_permission(Permission.EXPORT_DECISIONS)
RunId = Annotated[UUID, Query()]


def read_results(
    request: Request, actor: CurrentUser, session: Annotated[Session, Depends(get_session)]
) -> RiskResults:
    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    session.execute(text("SET TRANSACTION READ ONLY"))
    return RiskResults(session, request.app.state.settings.app_mode, actor)


ReadResults = Annotated[RiskResults, Depends(read_results)]


@router.post("/evaluations", status_code=201, responses={200: {"model": RunView}})
def create_evaluation(
    payload: EvaluationRequest,
    request: Request,
    response: Response,
    actor: Annotated[ActorContext, Depends(require_admin)],
) -> RunView:
    if payload.config_version is not None and payload.config_version != RULE_VERSION:
        raise EvaluationError("unsupported_configuration_version")
    database = get_database(request)
    if database is None:
        raise HTTPException(status_code=503)
    outcome = evaluate_batch(database, actor, payload.batch_id)
    response.status_code = 201 if outcome.created else 200
    with database.session() as session:
        return RiskResults(session, database.app_mode, actor).evaluation(outcome.run.run_id)


@router.get("/evaluations", dependencies=[Depends(require_read)])
def evaluations(
    filters: Annotated[EvaluationQuery, Query()], results: ReadResults
) -> list[RunView]:
    return results.evaluations(filters)


@router.get("/evaluations/{run_id}", dependencies=[Depends(require_read)])
def evaluation(run_id: UUID, results: ReadResults) -> RunView:
    return results.evaluation(run_id)


@router.get("/models", dependencies=[Depends(require_read)])
def models(filters: Annotated[PageQuery, Query()], results: ReadResults) -> list[ModelView]:
    return results.models(filters)


@router.get("/dashboard", dependencies=[Depends(require_read)])
def dashboard(run_id: RunId, results: ReadResults) -> Dashboard:
    return results.dashboard(run_id)


@router.get("/payouts", dependencies=[Depends(require_read)])
def payouts(filters: Annotated[PayoutQuery, Query()], results: ReadResults) -> PayoutPage:
    return results.payouts(filters)


@router.get("/payouts/{payout_id}", dependencies=[Depends(require_read)])
def payout(payout_id: PayoutId, run_id: RunId, results: ReadResults) -> PayoutDetail:
    return results.payout(run_id, payout_id)


@router.get("/fraud-clusters", dependencies=[Depends(require_read)])
def fraud_clusters(run_id: RunId, results: ReadResults) -> list[ClusterView]:
    return results.clusters(run_id)


@router.get(
    "/exports/decisions.csv",
    dependencies=[Depends(require_export)],
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_decisions(run_id: RunId, results: ReadResults) -> Response:
    return Response(
        content=render_csv(results.export_rows(run_id)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="decisions.csv"'},
    )
