from contextlib import ExitStack

from app.core.config import Settings
from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models import DatasetBatch
from app.models.values import FileType
from app.schemas.ingestion import ImportFailure, InputFile
from app.services.csv_source import import_options
from app.services.ingestion_service import ingest

PRESET_ID = "synthetic-v1"


def import_preset(
    database: Database, settings: Settings, actor: ActorContext, preset_id: str
) -> tuple[DatasetBatch, bool]:
    if preset_id != PRESET_ID:
        raise ImportFailure("unknown_preset", field="preset_id")
    preset_dir = settings.demo_preset_dir
    if preset_dir is None:
        raise ImportFailure("preset_unavailable", status=503)
    options = import_options("INITIAL", "2025-02-20T10:00:00+05:30")
    with ExitStack() as stack:
        try:
            paths = {kind: preset_dir / f"{kind}.csv" for kind in FileType}
            files = {
                kind: InputFile(path.name, stack.enter_context(path.open("rb")))
                for kind, path in paths.items()
            }
        except OSError:
            raise ImportFailure("preset_unavailable", status=503) from None
        return ingest(database, settings, actor, files, options)
