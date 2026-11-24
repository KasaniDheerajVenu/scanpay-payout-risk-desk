from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import HistoricalDecision, ReasonCode, SourceKind, enum_check

if TYPE_CHECKING:
    from app.models.dataset_batch import DatasetBatch
    from app.models.team import Team


class Payout(Base):
    __tablename__ = "payouts"
    __table_args__ = (
        CheckConstraint(enum_check("first_source_kind", SourceKind), name="source_kind_values"),
        CheckConstraint(
            enum_check("historical_decision", HistoricalDecision), name="historical_decision_values"
        ),
        CheckConstraint(
            enum_check("historical_reason_code", ReasonCode), name="historical_reason_code_values"
        ),
        CheckConstraint("covers_from <= covers_to", name="valid_coverage"),
        CheckConstraint("txn_count >= 0", name="nonnegative_transaction_count"),
        CheckConstraint(
            "created_date_ist = (created_at AT TIME ZONE 'Asia/Kolkata')::date",
            name="ist_creation_date",
        ),
        CheckConstraint("historical_decided_at >= created_at", name="historical_timestamp_order"),
        CheckConstraint(
            """
            (first_source_kind = 'HISTORY'
             AND source_status IS NULL
             AND historical_decision IS NOT NULL AND historical_decided_at IS NOT NULL
             AND disposition_import_batch_id IS NULL AND disposition_recorded_at IS NULL)
            OR
            (first_source_kind = 'PENDING'
             AND source_status IS NOT DISTINCT FROM 'PENDING_DECISION'
             AND (
                (historical_decision IS NULL AND historical_decided_at IS NULL
                 AND historical_decided_by IS NULL AND historical_reason_code IS NULL
                 AND historical_reason IS NULL AND disposition_import_batch_id IS NULL
                 AND disposition_recorded_at IS NULL)
                OR
                (historical_decision IS NOT NULL AND historical_decided_at IS NOT NULL
                 AND disposition_import_batch_id IS NOT NULL
                 AND disposition_recorded_at IS NOT NULL)
             ))
            """,
            name="source_disposition",
        ),
        Index("ix_payouts_team_coverage", "team_id", "covers_from", "covers_to"),
        Index("ix_payouts_created_date_ist", "created_date_ist"),
        Index(
            "ix_payouts_historical_decided_at",
            "historical_decided_at",
            postgresql_where=text("historical_decided_at IS NOT NULL"),
        ),
    )

    payout_id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_date_ist: Mapped[date]
    covers_from: Mapped[date]
    covers_to: Mapped[date]
    txn_count: Mapped[int]
    payout_amount_cents: Mapped[int] = mapped_column(BigInteger)
    historical_decision: Mapped[str | None] = mapped_column(Text)
    historical_decided_by: Mapped[str | None] = mapped_column(Text)
    historical_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    historical_reason_code: Mapped[str | None] = mapped_column(Text)
    historical_reason: Mapped[str | None] = mapped_column(Text)
    source_status: Mapped[str | None] = mapped_column(Text)
    first_import_batch_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_batches.batch_id"))
    first_source_kind: Mapped[str] = mapped_column(Text)
    disposition_import_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dataset_batches.batch_id")
    )
    disposition_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    team: Mapped[Team] = relationship(back_populates="payouts")
    first_import_batch: Mapped[DatasetBatch] = relationship(foreign_keys=[first_import_batch_id])
    disposition_import_batch: Mapped[DatasetBatch | None] = relationship(
        foreign_keys=[disposition_import_batch_id]
    )
