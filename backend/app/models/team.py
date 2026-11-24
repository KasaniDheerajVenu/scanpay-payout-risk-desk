from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import Profession, TeamStatus, enum_check

if TYPE_CHECKING:
    from app.models.dataset_batch import DatasetBatch
    from app.models.payout import Payout
    from app.models.transaction import Transaction


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(enum_check("profession", Profession), name="profession_values"),
        CheckConstraint(enum_check("status", TeamStatus), name="status_values"),
    )

    team_id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_name: Mapped[str] = mapped_column(Text)
    profession: Mapped[str] = mapped_column(Text)
    owner_name: Mapped[str] = mapped_column(Text)
    owner_phone: Mapped[str] = mapped_column(Text)
    business_address: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    onboarded_on: Mapped[date]
    status: Mapped[str] = mapped_column(Text)
    suspended_on: Mapped[date | None]
    payout_bank_routing: Mapped[str] = mapped_column(Text)
    payout_bank_last4: Mapped[str] = mapped_column(Text)
    signup_device_id: Mapped[str | None] = mapped_column(Text)
    first_import_batch_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_batches.batch_id"))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    first_import_batch: Mapped[DatasetBatch] = relationship()
    transactions: Mapped[list[Transaction]] = relationship(back_populates="team")
    payouts: Mapped[list[Payout]] = relationship(back_populates="team")
