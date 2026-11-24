from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import PaymentMethod, TransactionStatus, enum_check

if TYPE_CHECKING:
    from app.models.dataset_batch import DatasetBatch
    from app.models.team import Team


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(enum_check("payment_method", PaymentMethod), name="payment_method_values"),
        CheckConstraint(enum_check("status", TransactionStatus), name="status_values"),
        CheckConstraint(
            "collection_date_ist = (created_at AT TIME ZONE 'Asia/Kolkata')::date",
            name="ist_collection_date",
        ),
        Index("ix_transactions_team_collection_date", "team_id", "collection_date_ist"),
        Index(
            "ix_transactions_card_fingerprint",
            "card_fingerprint",
            postgresql_where=text("card_fingerprint IS NOT NULL"),
        ),
    )

    txn_id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collection_date_ist: Mapped[date]
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    tip_cents: Mapped[int] = mapped_column(BigInteger)
    payment_method: Mapped[str] = mapped_column(Text)
    card_fingerprint: Mapped[str | None] = mapped_column(Text)
    card_country: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    customer_name: Mapped[str] = mapped_column(Text)
    customer_zip: Mapped[str] = mapped_column(Text)
    first_import_batch_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_batches.batch_id"))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    team: Mapped[Team] = relationship(back_populates="transactions")
    first_import_batch: Mapped[DatasetBatch] = relationship()
