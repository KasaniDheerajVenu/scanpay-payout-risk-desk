from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import EvaluationStatus, enum_check

if TYPE_CHECKING:
    from app.models.dataset_batch import DatasetBatch
    from app.models.fraud_cluster import FraudCluster
    from app.models.model_version import ModelVersion
    from app.models.payout_decision import PayoutDecision
    from app.models.user import User


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(enum_check("status", EvaluationStatus), name="status_values"),
        CheckConstraint("scope_key ~ '[^[:space:]]'", name="scope_nonblank"),
        CheckConstraint(
            "status <> 'COMPLETED' OR (snapshot_at IS NOT NULL "
            "AND input_digest IS NOT NULL AND completed_at IS NOT NULL)",
            name="completed_has_snapshot",
        ),
        Index("ix_evaluation_runs_batch_status", "batch_id", "status"),
        Index(
            "uq_evaluation_runs_completed_batch_digest",
            "batch_id",
            "input_digest",
            unique=True,
            postgresql_where=text("status = 'COMPLETED'"),
        ),
    )

    run_id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_batches.batch_id"))
    scope_key: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.model_version_id")
    )
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_digest: Mapped[str | None] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'QUEUED'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    history_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    batch: Mapped[DatasetBatch] = relationship(back_populates="runs")
    requester: Mapped[User] = relationship()
    model_version: Mapped[ModelVersion | None] = relationship()
    clusters: Mapped[list[FraudCluster]] = relationship(back_populates="run")
    decisions: Mapped[list[PayoutDecision]] = relationship(back_populates="run")
