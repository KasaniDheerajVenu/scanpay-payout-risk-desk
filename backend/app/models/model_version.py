from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Float, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.values import MLStatus, enum_check


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("scope_key", "training_fingerprint", "config"),
        CheckConstraint(enum_check("status", MLStatus), name="status_values"),
        CheckConstraint(
            "training_observation_count >= 0 AND validation_observation_count >= 0",
            name="nonnegative_counts",
        ),
        CheckConstraint("validation_cutoff_at >= fit_cutoff_at", name="chronological_cutoffs"),
    )

    model_version_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    scope_key: Mapped[str] = mapped_column(Text)
    training_fingerprint: Mapped[str] = mapped_column(Text)
    algorithm: Mapped[str] = mapped_column(Text)
    feature_schema_version: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fit_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_observation_count: Mapped[int]
    validation_observation_count: Mapped[int]
    anomaly_threshold: Mapped[float | None] = mapped_column(Float(53))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
