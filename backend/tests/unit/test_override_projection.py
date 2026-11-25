from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import PayoutDecision, PayoutDecisionHistory, User
from app.models.values import Decision
from app.schemas.decisions import DecisionSource, OverrideRequest
from app.services.decision_overrides import project_effective


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "   "),
        ("reason", "a" * 1001),
        ("expected_version", True),
        ("expected_version", "1"),
        ("expected_version", 0),
        ("decision", "RELEASE"),
        ("changed_by", str(uuid4())),
        ("scope_key", "local"),
        ("evidence", [{"type": "transaction", "id": "bad\nvalue"}]),
        ("evidence", [{"type": "transaction", "id": "safe", "bank_number": "fake"}]),
    ],
)
def test_override_request_validates_reason_version_and_trusted_fields(field, value):
    payload = {
        "run_id": str(uuid4()),
        "decision": "APPROVE",
        "reason": "Verified the invoice.",
        "expected_version": 1,
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        OverrideRequest.model_validate(payload)


def test_effective_projection_uses_event_without_touching_original():
    timestamp = datetime(2025, 4, 1, tzinfo=UTC)
    original = PayoutDecision(
        payout_id="synthetic-payout",
        run_id=uuid4(),
        decision_id=uuid4(),
        cluster_id=uuid4(),
        reason="Original synthetic decision",
        decision=Decision.REJECT,
        evidence=[],
        created_at=timestamp,
    )
    actor = User(user_id=uuid4(), display_name="Synthetic Analyst")
    event = PayoutDecisionHistory(
        history_id=uuid4(),
        changed_by=actor.user_id,
        version=2,
        previous_decision=Decision.REJECT,
        new_decision=Decision.APPROVE,
        change_reason="Verified the synthetic invoice.",
        evidence=[],
        created_at=timestamp,
    )
    baseline = project_effective(original, None, None)
    revised = project_effective(original, event, actor.display_name)
    assert (baseline.effective_version, baseline.decision_source) == (1, DecisionSource.AUTOMATED)
    assert baseline.cluster_id == original.cluster_id and baseline.actor is None
    assert (revised.effective_version, revised.decision_source) == (2, DecisionSource.HUMAN)
    assert revised.original_decision == Decision.REJECT and revised.cluster_id is None
    assert revised.actor.user_id == actor.user_id and revised.timestamp == timestamp
    assert revised.model_dump(by_alias=True)["effective_decision"] == Decision.APPROVE
