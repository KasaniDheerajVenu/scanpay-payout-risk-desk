from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import BatchMode, BatchStatus, enum_check

if TYPE_CHECKING:
    from app.models.evaluation_run import EvaluationRun
    from app.models.uploaded_file import UploadedFile
    from app.models.user import User


class DatasetBatch(Base):
    __tablename__ = "dataset_batches"
    __table_args__ = (
        CheckConstraint(enum_check("mode", BatchMode), name="mode_values"),
        CheckConstraint(enum_check("status", BatchStatus), name="status_values"),
        CheckConstraint("scope_key ~ '[^[:space:]]'", name="scope_nonblank"),
        CheckConstraint(
            "status <> 'READY' OR (input_digest IS NOT NULL AND imported_at IS NOT NULL)",
            name="ready_has_import",
        ),
        Index(
            "uq_dataset_batches_ready_scope_digest",
            "scope_key",
            "input_digest",
            unique=True,
            postgresql_where=text("status = 'READY'"),
        ),
    )

    batch_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    scope_key: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    mode: Mapped[str] = mapped_column(Text)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'UPLOADED'"))
    input_digest: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    uploader: Mapped[User] = relationship()
    files: Mapped[list[UploadedFile]] = relationship(back_populates="batch")
    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="batch")
