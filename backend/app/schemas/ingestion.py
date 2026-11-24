from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO

from app.models.values import BatchMode, FileType


@dataclass(frozen=True)
class ImportOptions:
    mode: BatchMode
    cutoff: datetime
    assessment: bool = False


@dataclass(frozen=True)
class InputFile:
    filename: str
    stream: BinaryIO


@dataclass(frozen=True)
class SourceRow:
    line: int
    values: dict[str, Any]


@dataclass(frozen=True)
class ParsedFile:
    file_type: FileType
    filename: str
    sha256: str
    bytes_count: int
    rows: list[SourceRow]


class ImportFailure(Exception):
    def __init__(
        self,
        issue: str,
        *,
        status: int = 422,
        file_type: FileType | None = None,
        row: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(issue)
        self.status = status
        self.details: dict[str, str | int] = {"code": issue}
        if file_type is not None:
            self.details["file_type"] = file_type.value
        if row is not None:
            self.details["row"] = row
        if field is not None:
            self.details["field"] = field
