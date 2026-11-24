from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.values import BatchMode, BatchStatus, FileType


class DemoImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    preset_id: str


class ImportCounts(BaseModel):
    rows: int = 0
    inserted: int = 0
    unchanged: int = 0
    dispositions_completed: int = 0


class ValidationIssue(BaseModel):
    code: str
    file_type: FileType | None = None
    row: int | None = None
    field: str | None = None


class FileValidation(BaseModel):
    dispositions_completed: int = 0
    incomplete_historical_windows: int = 0


class FileMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    file_type: FileType
    filename: str
    sha256: str
    bytes_count: int
    row_count: int
    inserted_count: int
    unchanged_count: int
    validation_summary: FileValidation


class DatasetSummary(BaseModel):
    batch_id: UUID
    status: BatchStatus
    mode: BatchMode
    cutoff: datetime
    imported_at: datetime | None
    counts: ImportCounts
    validation: ValidationIssue | None


class DatasetDetail(DatasetSummary):
    files: list[FileMetadata]
