from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.api.dataset_upload import bounded_stream, receive_upload
from app.api.dependencies import get_database, require_permission
from app.core.config import Settings
from app.core.permissions import Permission
from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models import DatasetBatch
from app.models.values import FileType
from app.repositories.datasets import get_batch, list_batches
from app.schemas.datasets import (
    DatasetDetail,
    DatasetSummary,
    DemoImportRequest,
    FileMetadata,
    ImportCounts,
    ValidationIssue,
)
from app.schemas.ingestion import ImportFailure
from app.services.demo_datasets import import_preset
from app.services.ingestion_service import dataset_scope, ingest

router = APIRouter(prefix="/api/datasets", tags=["Datasets"])
Uploader = Annotated[ActorContext, Depends(require_permission(Permission.UPLOAD_DATASETS))]
DemoAdmin = Annotated[ActorContext, Depends(require_permission(Permission.IMPORT_SYNTHETIC_DEMO))]
require_read = require_permission(Permission.READ_RISK)


def require_database(request: Request) -> Database:
    database = get_database(request)
    if database is None:
        raise HTTPException(status_code=503)
    return database


DatabaseDependency = Annotated[Database, Depends(require_database)]


def get_dataset_settings(request: Request) -> Settings:
    return request.app.state.settings


DatasetSettings = Annotated[Settings, Depends(get_dataset_settings)]


def summary(batch: DatasetBatch) -> DatasetSummary:
    return DatasetSummary(
        batch_id=batch.batch_id,
        mode=batch.mode,
        status=batch.status,
        cutoff=batch.cutoff_at,
        imported_at=batch.imported_at,
        counts=ImportCounts(
            rows=sum(file.row_count for file in batch.files),
            inserted=sum(file.inserted_count for file in batch.files),
            unchanged=sum(file.unchanged_count for file in batch.files),
            dispositions_completed=sum(
                file.validation_summary.get("dispositions_completed", 0) for file in batch.files
            ),
        ),
        validation=ValidationIssue.model_validate(batch.error_summary)
        if batch.error_summary
        else None,
    )


def detail(batch: DatasetBatch) -> DatasetDetail:
    return DatasetDetail(
        **summary(batch).model_dump(),
        files=[
            FileMetadata.model_validate(file)
            for file in sorted(batch.files, key=lambda file: file.file_type)
        ],
    )


@router.post(
    "/upload",
    status_code=201,
    responses={200: {"model": DatasetDetail, "description": "Existing import"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["mode", "cutoff", "latest_transactions", "pending_payouts"],
                        "properties": {
                            "mode": {"type": "string", "enum": ["INITIAL", "INCREMENTAL"]},
                            "cutoff": {"type": "string"},
                            "assessment": {"type": "boolean", "default": False},
                            **{
                                kind.value: {"type": "string", "format": "binary"}
                                for kind in FileType
                            },
                        },
                    }
                }
            },
        }
    },
)
async def upload(
    request: Request,
    response: Response,
    actor: Uploader,
    database: DatabaseDependency,
    settings: DatasetSettings,
) -> DatasetDetail:
    # Declaring File/Form parameters would let FastAPI read the body before its auth dependencies.
    async with receive_upload(request, settings) as (files, options):
        batch, created = await run_in_threadpool(ingest, database, settings, actor, files, options)
    if not created:
        response.status_code = 200
    return detail(batch)


@router.post(
    "/demo-import",
    status_code=201,
    responses={200: {"model": DatasetDetail, "description": "Existing import"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": DemoImportRequest.model_json_schema()}},
        }
    },
)
async def demo_import(
    request: Request,
    response: Response,
    actor: DemoAdmin,
    database: DatabaseDependency,
    settings: DatasetSettings,
) -> DatasetDetail:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise ImportFailure("invalid_demo_request")
    try:
        payload = DemoImportRequest.model_validate_json(
            b"".join([chunk async for chunk in bounded_stream(request, 4096)])
        )
    except (ValidationError, ClientDisconnect):
        raise ImportFailure("invalid_demo_request") from None
    batch, created = await run_in_threadpool(
        import_preset, database, settings, actor, payload.preset_id
    )
    if not created:
        response.status_code = 200
    return detail(batch)


def read_session(database: DatabaseDependency) -> Iterator[Session]:
    with database.session() as session:
        yield session


ReadSession = Annotated[Session, Depends(read_session)]


@router.get("", dependencies=[Depends(require_read)])
def dataset_list(session: ReadSession, settings: DatasetSettings) -> list[DatasetSummary]:
    return [summary(batch) for batch in list_batches(session, dataset_scope(settings.app_mode))]


@router.get("/{batch_id}", dependencies=[Depends(require_read)])
def dataset_detail(
    batch_id: UUID, session: ReadSession, settings: DatasetSettings
) -> DatasetDetail:
    batch = get_batch(session, dataset_scope(settings.app_mode), batch_id)
    if batch is None:
        raise HTTPException(status_code=404)
    return detail(batch)
