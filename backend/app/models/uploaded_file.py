from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.values import FileType, enum_check

if TYPE_CHECKING:
    from app.models.dataset_batch import DatasetBatch


class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        UniqueConstraint("batch_id", "file_type"),
        CheckConstraint(enum_check("file_type", FileType), name="file_type_values"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="valid_sha256"),
        CheckConstraint(
            "filename ~ '[^[:space:]]' AND filename NOT IN ('.', '..') "
            "AND strpos(filename, '/') = 0 AND strpos(filename, chr(92)) = 0",
            name="sanitized_filename",
        ),
        CheckConstraint(
            "bytes_count >= 0 AND row_count >= 0 AND inserted_count >= 0 "
            "AND unchanged_count >= 0 AND inserted_count + unchanged_count <= row_count",
            name="valid_counts",
        ),
        CheckConstraint("jsonb_typeof(imported_source_ids) = 'array'", name="membership_is_array"),
    )

    file_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_batches.batch_id"))
    file_type: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    bytes_count: Mapped[int] = mapped_column(BigInteger)
    row_count: Mapped[int] = mapped_column()
    inserted_count: Mapped[int] = mapped_column()
    unchanged_count: Mapped[int] = mapped_column()
    validation_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    imported_source_ids: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )

    batch: Mapped[DatasetBatch] = relationship(back_populates="files")
