from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.evaluation_run import EvaluationRun
    from app.models.payout import Payout


class FraudCluster(Base):
    __tablename__ = "fraud_clusters"
    __table_args__ = (
        UniqueConstraint("run_id", "stable_case_key"),
        UniqueConstraint("run_id", "cluster_id"),
        CheckConstraint(
            "reason_code IN ('FRAUD_CARD_RING', 'FRAUD_CARD_TESTING', 'FRAUD_ACCOUNT_TAKEOVER')",
            name="fraud_reason_values",
        ),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_is_array"),
    )

    cluster_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    stable_case_key: Mapped[str] = mapped_column(Text)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_runs.run_id"))
    seed_payout_id: Mapped[str] = mapped_column(ForeignKey("payouts.payout_id"))
    reason_code: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )

    run: Mapped[EvaluationRun] = relationship(back_populates="clusters")
    seed_payout: Mapped[Payout] = relationship()
