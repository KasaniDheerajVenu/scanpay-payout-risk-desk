from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import Decision, MLStatus, enum_check

if TYPE_CHECKING:
    from app.models.evaluation_run import EvaluationRun
    from app.models.fraud_cluster import FraudCluster
    from app.models.model_version import ModelVersion
    from app.models.payout import Payout
    from app.models.payout_decision_history import PayoutDecisionHistory


class PayoutDecision(Base):
    __tablename__ = "payout_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "payout_id"),
        ForeignKeyConstraint(
            ["run_id", "cluster_id"], ["fraud_clusters.run_id", "fraud_clusters.cluster_id"]
        ),
        CheckConstraint(enum_check("decision", Decision), name="decision_values"),
        CheckConstraint(enum_check("ml_status", MLStatus), name="ml_status_values"),
        CheckConstraint(
            "(decision = 'REJECT') = (cluster_id IS NOT NULL)", name="reject_has_cluster"
        ),
        CheckConstraint("reason ~ '[^[:space:]]'", name="reason_nonblank"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_is_array"),
        CheckConstraint("ml_is_anomaly IS NULL OR ml_score IS NOT NULL", name="anomaly_has_score"),
    )

    decision_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_runs.run_id"))
    payout_id: Mapped[str] = mapped_column(ForeignKey("payouts.payout_id"))
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.model_version_id")
    )
    decision: Mapped[str] = mapped_column(Text)
    cluster_id: Mapped[UUID | None]
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    findings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    ml_score: Mapped[float | None] = mapped_column(Float(53))
    ml_is_anomaly: Mapped[bool | None]
    ml_status: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[EvaluationRun] = relationship(back_populates="decisions")
    payout: Mapped[Payout] = relationship()
    model_version: Mapped[ModelVersion | None] = relationship()
    cluster: Mapped[FraudCluster | None] = relationship(viewonly=True)
    history: Mapped[list[PayoutDecisionHistory]] = relationship(back_populates="original_decision")
