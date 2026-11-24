from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from app.models import DatasetBatch
from app.models.values import BatchMode, BatchStatus


def lock_imports(session: Session) -> None:
    # All scopes share physical source primary keys, so this lock is database-wide.
    session.execute(text("SELECT pg_advisory_xact_lock(571198004)"))


def ready_initial_exists(session: Session, scope: str) -> bool:
    return bool(
        session.scalar(
            select(DatasetBatch.batch_id)
            .where(
                DatasetBatch.scope_key == scope,
                DatasetBatch.mode == BatchMode.INITIAL,
                DatasetBatch.status == BatchStatus.READY,
            )
            .limit(1)
        )
    )


def find_ready_import(session: Session, scope: str, digest: str) -> DatasetBatch | None:
    return session.scalar(
        select(DatasetBatch)
        .where(
            DatasetBatch.scope_key == scope,
            DatasetBatch.input_digest == digest,
            DatasetBatch.status == BatchStatus.READY,
        )
        .options(selectinload(DatasetBatch.files))
    )


def list_batches(session: Session, scope: str) -> list[DatasetBatch]:
    return list(
        session.scalars(
            select(DatasetBatch)
            .where(DatasetBatch.scope_key == scope)
            .options(selectinload(DatasetBatch.files))
            .order_by(
                DatasetBatch.imported_at.desc().nulls_last(),
                DatasetBatch.cutoff_at.desc(),
                DatasetBatch.batch_id,
            )
        )
    )


def get_batch(session: Session, scope: str, batch_id: UUID) -> DatasetBatch | None:
    return session.scalar(
        select(DatasetBatch)
        .where(DatasetBatch.scope_key == scope, DatasetBatch.batch_id == batch_id)
        .options(selectinload(DatasetBatch.files))
    )
