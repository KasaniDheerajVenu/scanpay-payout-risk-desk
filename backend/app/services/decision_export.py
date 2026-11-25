import csv
import re
from collections.abc import Iterable
from io import StringIO

from app.models.values import Decision
from app.schemas.decisions import SOURCE_ID_PATTERN, EffectiveDecision
from app.schemas.evaluation import EvaluationError

HEADER = ("payout_id", "team_id", "decision", "fraud_cluster", "reasons", "evidence")
LABELS = {
    Decision.APPROVE: "Approve",
    Decision.MANUAL_REVIEW: "Manual review",
    Decision.REJECT: "Reject",
}
DANGEROUS_PREFIX = re.compile(r"^[\s\x00-\x1f\ufeff]*[=+@\-＝＋＠－]|^[\t\r\n]")
SOURCE_ID = re.compile(SOURCE_ID_PATTERN)


def spreadsheet_safe(reason: str) -> str:
    return "'" + reason if DANGEROUS_PREFIX.match(reason) else reason


def render_csv(rows: Iterable[tuple[EffectiveDecision, str]]) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADER)
    for state, team_id in rows:
        evidence = sorted({ref.id for ref in state.evidence})
        if any(not SOURCE_ID.fullmatch(key) for key in (state.payout_id, team_id, *evidence)):
            raise EvaluationError("invalid_export_identifier", 503)
        if state.decision == Decision.REJECT and state.cluster_id is None:
            raise EvaluationError("missing_export_cluster", 503)
        writer.writerow(
            (
                state.payout_id,
                team_id,
                LABELS[state.decision],
                state.cluster_id if state.decision == Decision.REJECT else "",
                spreadsheet_safe(state.reason),
                ";".join(evidence),
            )
        )
    return output.getvalue()
