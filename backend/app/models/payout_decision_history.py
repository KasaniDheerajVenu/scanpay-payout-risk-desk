from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import Decision, enum_check

if TYPE_CHECKING:
    from app.models.fraud_cluster import FraudCluster
    from app.models.payout_decision import PayoutDecision
    from app.models.user import User


class PayoutDecisionHistory(Base):
    __tablename__ = "payout_decision_history"
    __table_args__ = (
        UniqueConstraint("decision_id", "scope_key", "version"),
        CheckConstraint("version >= 2", name="analyst_version"),
        CheckConstraint("scope_key ~ '[^[:space:]]'", name="scope_nonblank"),
        CheckConstraint(enum_check("previous_decision", Decision), name="previous_decision_values"),
        CheckConstraint(enum_check("new_decision", Decision), name="new_decision_values"),
        CheckConstraint(
            "(new_decision = 'REJECT') = (cluster_id IS NOT NULL)", name="reject_has_cluster"
        ),
        CheckConstraint("change_reason ~ '[^[:space:]]'", name="reason_nonblank"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_is_array"),
    )

    history_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    decision_id: Mapped[UUID] = mapped_column(ForeignKey("payout_decisions.decision_id"))
    scope_key: Mapped[str] = mapped_column(Text)
    version: Mapped[int]
    previous_decision: Mapped[str] = mapped_column(Text)
    new_decision: Mapped[str] = mapped_column(Text)
    cluster_id: Mapped[UUID | None] = mapped_column(ForeignKey("fraud_clusters.cluster_id"))
    change_reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    changed_by: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    original_decision: Mapped[PayoutDecision] = relationship(back_populates="history")
    cluster: Mapped[FraudCluster | None] = relationship()
    actor: Mapped[User] = relationship()
