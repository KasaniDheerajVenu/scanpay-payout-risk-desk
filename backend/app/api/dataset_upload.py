from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from python_multipart.multipart import parse_options_header
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from app.core.config import Settings
from app.models.values import FileType
from app.schemas.ingestion import ImportFailure, ImportOptions, InputFile
from app.services.csv_source import import_options

UPLOAD_FIELDS = {"mode", "cutoff", "assessment"}


class BoundedMultipart(MultiPartParser):
    def __init__(self, request: Request, settings: Settings) -> None:
        super().__init__(
            request.headers,
            bounded_stream(request, settings.upload_max_total_bytes),
            max_files=len(FileType),
            max_fields=len(UPLOAD_FIELDS),
            max_part_size=128,
        )
        self.file_limit = settings.upload_max_file_bytes
        self.part_bytes = 0
        self.header_bytes = 0
        self.complete = False

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self.part_bytes = self.header_bytes = 0

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.check_header_size(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.check_header_size(end - start)
        super().on_header_value(data, start, end)

    def check_header_size(self, length: int) -> None:
        self.header_bytes += length
        if self.header_bytes > 8192:
            raise ImportFailure("multipart_headers_too_large", status=413)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.part_bytes += end - start
        limit = self.file_limit if self._current_part.file is not None else self.max_part_size
        if self.part_bytes > limit:
            raise ImportFailure("upload_too_large", status=413)
        super().on_part_data(data, start, end)

    def on_end(self) -> None:
        self.complete = True


async def bounded_stream(request: Request, limit: int) -> AsyncIterator[bytes]:
    consumed = 0
    async for chunk in request.stream():
        consumed += len(chunk)
        if consumed > limit:
            raise ImportFailure("upload_too_large", status=413)
        yield chunk


def upload_parts(form: FormData) -> tuple[dict[FileType, InputFile], ImportOptions]:
    files: dict[FileType, InputFile] = {}
    fields: dict[str, str] = {}
    for key, value in form.multi_items():
        if key in fields or key in files:
            raise ImportFailure("repeated_multipart_field")
        if isinstance(value, UploadFile) and key in FileType:
            files[FileType(key)] = InputFile(value.filename or "upload.csv", value.file)
        elif isinstance(value, str) and key in UPLOAD_FIELDS:
            fields[key] = value
        else:
            raise ImportFailure("unexpected_multipart_field")
    for required in ("mode", "cutoff"):
        if required not in fields:
            raise ImportFailure("missing_multipart_field", field=required)
    assessment = fields.get("assessment", "false")
    if assessment not in {"true", "false"}:
        raise ImportFailure("invalid_assessment_mode", field="assessment")
    return files, import_options(fields["mode"], fields["cutoff"], assessment == "true")


@asynccontextmanager
async def receive_upload(
    request: Request, settings: Settings
) -> AsyncIterator[tuple[dict[FileType, InputFile], ImportOptions]]:
    media_type, params = parse_options_header(request.headers.get("content-type"))
    if media_type != b"multipart/form-data" or not 1 <= len(params.get(b"boundary", b"")) <= 70:
        raise ImportFailure("invalid_multipart")
    parser = BoundedMultipart(request, settings)
    try:
        form = await parser.parse()
    except BaseException as exc:
        for file in parser._files_to_close_on_error:
            file.close()
        if isinstance(exc, (MultiPartException, ClientDisconnect)):
            raise ImportFailure("invalid_multipart") from None
        if isinstance(exc, OSError):
            raise ImportFailure("input_unavailable", status=503) from None
        raise
    try:
        if not parser.complete:
            raise ImportFailure("invalid_multipart")
        yield upload_parts(form)
    finally:
        await form.close()
