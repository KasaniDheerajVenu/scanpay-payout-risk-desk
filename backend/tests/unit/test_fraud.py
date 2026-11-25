from dataclasses import replace
from datetime import date, timedelta

import pytest
from test_statistics import (
    FIRST_IMPORT,
    LATER_IMPORT,
    OBSERVED,
    POLICY,
    PROVENANCE,
    attempt,
    historical,
    raw,
    snapshot,
)

from app.models.values import (
    HistoricalDecision,
    PaymentMethod,
    Profession,
    ReasonCode,
    TeamStatus,
    TransactionStatus,
)
from app.risk_engine.fraud_evidence import CaseMapping, MappedCase, resolve_seeds
from app.risk_engine.fraud_graph import Node, build_graph, merge_projection
from app.risk_engine.inputs import TeamInput
from app.schemas.fraud_findings import Exposure, NodeKind, Relationship, Strength
from app.services.fraud_analysis_service import (
    analyze_fraud,
    analyze_fraud_and_statistics,
    fraud_config,
)
from app.services.statistical_analysis_service import HistoryUse
from scripts.evaluate_batch import load_mapping

RING = ReasonCode.FRAUD_CARD_RING
TESTING = ReasonCode.FRAUD_CARD_TESTING
ATO = ReasonCode.FRAUD_ACCOUNT_TAKEOVER
CAPTURED = TransactionStatus.CAPTURED
DECLINED = TransactionStatus.DECLINED
REFUNDED = TransactionStatus.REFUNDED
PHONE = "+00 (555) 0123"
DEVICE = "synthetic-device-X"
BANK = "001002003"


def tx(
    key,
    team,
    fingerprint,
    weeks=0,
    status=CAPTURED,
    method=PaymentMethod.CARD,
    provenance=PROVENANCE,
):
    day = OBSERVED.date() - timedelta(weeks=weeks, days=3)
    return attempt(
        key,
        team_id=team,
        fingerprint=fingerprint,
        day=day,
        status=status,
        method=method,
        provenance=provenance,
    )


def team(key, phone="", device=None, routing="", last4="", status=TeamStatus.ACTIVE):
    return TeamInput(
        key,
        "private synthetic team name",
        Profession.PLUMBING,
        "private owner name",
        phone,
        "private address",
        "Synthetic City",
        "ST",
        date(2024, 1, 1),
        status,
        None,
        routing,
        last4,
        device,
        PROVENANCE,
    )


def pending(key, owner, *attempts):
    return raw(key, team=owner, attempts=attempts)


def seed(key, owner, card, weeks=5, *, reason=RING, evidence=None, extra=(), decision=None):
    transaction = tx(f"{key}-tx", owner, card, weeks)
    source = raw(key, team=owner, weeks=weeks, attempts=(transaction, *extra))
    row = historical(source, reason=reason, decision=HistoricalDecision.REJECTED, decided=decision)
    text = evidence if evidence is not None else f"implicated transaction: {transaction.txn_id}"
    return replace(row, disposition=replace(row.disposition, reason=text))


def inputs(history, current, *teams):
    return replace(snapshot(history, current), teams=teams)


def test_local_reviewed_mapping_loads_ids_and_requires_a_timezone(tmp_path):
    path = tmp_path / "case-review.json"
    valid = (
        '{"version":"synthetic-v1","cases":[{"seed_payout_id":"case-1",'
        '"transaction_ids":["txn-1"],"available_at":"2025-03-05T09:00:00+05:30",'
        '"implicated_profile_kinds":["signup_device"]}]}'
    )
    path.write_text(valid, encoding="utf-8")
    mapping = load_mapping(path)
    assert mapping.cases[0].transaction_ids == ("txn-1",)
    assert mapping.cases[0].implicated_profile_kinds == (NodeKind.DEVICE,)
    path.write_text(valid.replace("+05:30", ""), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mapping(path)


@pytest.mark.parametrize("issue", [None, "ambiguous_case_team", "ambiguous_case_description"])
def test_named_ring_requires_unique_teams_and_shared_captured_cards(issue):
    note = "The same 2 cards paid Maple Workshop and Cedar Works."
    if issue == "ambiguous_case_description":
        note = "It is unclear whether the same 2 cards paid Maple Workshop and Cedar Works."
    source = seed(
        "ring",
        "one",
        "shared-first",
        evidence=note,
        extra=(tx("second", "one", "shared-second", 5), tx("incidental", "one", "other", 5)),
    )
    peer = historical(
        raw(
            "peer",
            team="two",
            weeks=5,
            attempts=(
                tx("peer-first", "two", "shared-first", 5),
                tx("peer-second", "two", "shared-second", 5),
            ),
        )
    )
    current = pending("new", "three", tx("new-tx", "three", "shared-first"))
    metadata = (
        replace(team("one"), team_name="Maple Workshop"),
        replace(team("two"), team_name="Cedar Works"),
        replace(
            team("three"),
            team_name="Cedar Works" if issue == "ambiguous_case_team" else "Birch Repair",
        ),
    )
    data = inputs((source, peer), current, *metadata)
    resolved, unresolved = resolve_seeds(data)
    result = analyze_fraud(data)
    if issue:
        assert not resolved and unresolved[0].code == issue
        assert not result.pending[0].established_exposure
    else:
        assert not unresolved
        assert {item.txn_id for item in resolved[0].attempts} == {"ring-tx", "second"}
        assert {item.txn_id for item in resolved[0].witnesses} == {"peer-first", "peer-second"}
        assert result.pending[0].established_exposure


@pytest.mark.parametrize("after_capture", [False, True])
def test_named_testing_requires_earlier_small_attempts_on_each_peer(after_capture):
    row = seed(
        "testing",
        "one",
        "cross-card",
        reason=TESTING,
        evidence="1 card ran $2-4 test charges at Cedar Works and Birch Repair, "
        "then was charged $900+ here.",
    )
    row = replace(
        row,
        attempts=(replace(row.attempts[0], amount_cents=100_000),),
    )
    anchor = row.attempts[0]
    history = [row]
    for key, status in (("two", DECLINED), ("three", REFUNDED)):
        minutes = 15 if after_capture and key == "three" else -15
        witness = replace(
            tx(f"attempt-{key}", key, "cross-card", 5, status),
            amount_cents=300,
            created_at=anchor.created_at + timedelta(minutes=minutes),
        )
        history.append(historical(raw(f"old-{key}", team=key, weeks=5, attempts=(witness,))))
    data = inputs(
        tuple(history),
        pending("new", "four", tx("current", "four", "cross-card")),
        replace(team("one"), team_name="Maple Workshop"),
        replace(team("two"), team_name="Cedar Works"),
        replace(team("three"), team_name="Birch Repair"),
    )
    resolved, unresolved = resolve_seeds(data)
    assert bool(resolved) is not after_capture
    assert bool(unresolved) is after_capture
    if resolved:
        assert {item.status for item in resolved[0].witnesses} == {DECLINED, REFUNDED}


def test_only_verified_card_certificates_merge_cases_in_stable_components():
    a = seed("old-a", "owner-a", "collision", weeks=6)
    b = seed("old-b", "owner-b", "collision", weeks=5)
    c = seed("old-c", "owner-c", "independent-card", weeks=4)
    ambiguous = seed(
        "ambiguous", "owner-a", "ambiguous-card", evidence="Private unstructured note."
    )
    future = seed("future", "owner-a", "collision", decision=OBSERVED + timedelta(days=2))
    unavailable_seed = seed("unavailable", "owner-a", "collision")
    unavailable = replace(
        unavailable_seed,
        disposition=replace(
            unavailable_seed.disposition, available_at=LATER_IMPORT + timedelta(days=1)
        ),
    )
    victim = historical(
        raw(
            "victim",
            team="victim",
            weeks=2,
            attempts=(
                tx("victim-a", "victim", "collision", 2, DECLINED),
                tx("victim-b", "victim", "independent-card", 2, DECLINED),
            ),
        ),
        reason=ReasonCode.CARD_TESTING_VICTIM,
    )
    unsupported = seed("unsupported", "owner-a", "collision", reason=ReasonCode.OUTLIER_VERIFIED)
    current = pending("action", "collision", tx("current", "collision", "collision"))
    prepared = inputs(
        (b, c, a, victim, ambiguous, unavailable, unsupported, future),
        current,
        team("owner-a", PHONE, DEVICE),
        team("owner-c", PHONE, DEVICE),
        team("collision"),
        team("victim", PHONE, DEVICE),
    )
    result = analyze_fraud(prepared)
    reordered = replace(prepared, history=prepared.history[::-1], teams=prepared.teams[::-1])
    assert analyze_fraud(reordered) == result
    assert {cluster.case_payout_ids for cluster in result.clusters} == {
        ("old-a", "old-b"),
        ("old-c",),
    }
    assert (
        next(item for item in result.clusters if len(item.case_payout_ids) == 2).seed_payout_id
        == "old-a"
    )
    assert tuple(item.seed_payout_id for item in result.unresolved_seeds) == ("ambiguous",)
    seeds, _ = resolve_seeds(prepared)
    graph = build_graph(prepared, seeds)
    card, business = Node(NodeKind.CARD, "collision"), Node(NodeKind.TEAM, "collision")
    assert card in graph and business in graph and card != business
    assert business not in merge_projection(graph)
    stamp = graph[Node(NodeKind.CASE, "old-a")][card][Relationship.IMPLICATED_CARD]["witness"]
    assert stamp.supports_exposure and {item.id for item in stamp.evidence} == {
        "old-a",
        "old-a-tx",
        "owner-a",
    }
    bridge = seed(
        "bridge",
        "owner-d",
        "collision",
        weeks=3,
        extra=(tx("bridge-second", "owner-d", "independent-card", 3),),
        evidence="implicated transaction: bridge-tx\nimplicated transaction: bridge-second",
    )
    merged = analyze_fraud(replace(prepared, history=(a, b, c, bridge)))
    assert len(merged.clusters) == 1 and merged.clusters[0].case_payout_ids == (
        "bridge",
        "old-a",
        "old-b",
        "old-c",
    )
    assert merged.clusters[0].stable_case_key not in {
        item.stable_case_key for item in result.clusters
    }
    assert len(result.clusters) == 2


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CAPTURED, Exposure.ESTABLISHED),
        (DECLINED, Exposure.UNRESOLVED),
        (REFUNDED, Exposure.UNRESOLVED),
    ],
)
def test_payable_and_nonpayable_card_paths_are_distinct_and_redacted(status, expected):
    card = "sensitive-synthetic-card"
    case = seed("source", "owner-a", card)
    current = pending("action", "owner-b", tx("current", "owner-b", card, status=status))
    result = analyze_fraud(inputs((case,), current, team("owner-a", PHONE, DEVICE))).pending[0]
    assert result.status == expected
    assert result.established_exposure == (status == CAPTURED)
    if result.primary:
        path = result.primary.paths[0]
        assert [step.relationship for step in path.steps] == [
            Relationship.PAYOUT_ATTEMPT,
            Relationship.OBSERVED_CARD,
            Relationship.IMPLICATED_CARD,
            Relationship.CASE_TRANSACTION,
            Relationship.PAYOUT_ATTEMPT,
        ]
        assert {ref.id for ref in result.primary.current_evidence} == {
            "action",
            "current",
            "owner-b",
        }
        source_ids = {"action", "current", "source", "source-tx", "owner-a", "owner-b"}
        assert all(ref.id in source_ids for step in path.steps for ref in step.evidence)
    else:
        assert result.leads[0].relationship == Relationship.NONPAYABLE_CARD
    payload = result.model_dump_json()
    assert all(private not in payload for private in (card, PHONE, DEVICE, "private owner name"))


@pytest.mark.parametrize(
    ("current_phone", "current_device", "route", "last4", "expected"),
    [
        (PHONE, None, "", "", Exposure.WEAK),
        ("", DEVICE, "", "", Exposure.WEAK),
        ("", None, BANK, "9999", Exposure.NONE),
        ("", None, BANK, "0001", Exposure.WEAK),
        ("005550123", DEVICE, "", "", Exposure.ESTABLISHED),
    ],
)
def test_profile_corroboration_requires_both_explicit_keys(
    current_phone, current_device, route, last4, expected
):
    case = seed(
        "source",
        "owner-a",
        "case-card",
        evidence=(
            "implicated transaction: source-tx\n"
            f"implicated owner phone: {PHONE}\nimplicated signup device: {DEVICE}"
        ),
    )
    current = pending("action", "owner-b", tx("current", "owner-b", "different-card"))
    result = analyze_fraud(
        inputs(
            (case,),
            current,
            team("owner-a", PHONE, DEVICE, BANK, "0001"),
            team("owner-b", current_phone, current_device, route, last4),
        )
    ).pending[0]
    assert result.status == expected
    if expected == Exposure.ESTABLISHED:
        assert result.primary.strength == Strength.CORROBORATED
        assert len(result.primary.paths) == 2
        assert {step.end.kind for path in result.primary.paths for step in path.steps} >= {
            NodeKind.PHONE,
            NodeKind.DEVICE,
        }


def test_takeover_victim_missing_cards_and_incidental_activity_do_not_propagate():
    extra = tx("incidental", "owner-a", "legitimate-card", 5)
    case = seed(
        "source",
        "owner-a",
        "implicated-card",
        reason=ATO,
        extra=(extra,),
        evidence=(
            "implicated transaction: source-tx\n"
            f"implicated owner phone: {PHONE}\nimplicated signup device: {DEVICE}"
        ),
    )
    current = pending("action", "owner-a", tx("current", "owner-a", "legitimate-card"))
    result = analyze_fraud(inputs((case,), current, team("owner-a", PHONE, DEVICE))).pending[0]
    assert result.status == Exposure.WEAK
    assert {lead.relationship for lead in result.leads} == {
        Relationship.PRIOR_TEAM_CASE,
        Relationship.INCIDENTAL_CARD,
    }
    same_payer = pending("action", "owner-b", tx("payer", "owner-b", "implicated-card"))
    history = historical(
        raw(
            "prior",
            team="owner-b",
            weeks=2,
            attempts=(tx("prior-payer", "owner-b", "implicated-card", 2),),
        )
    )
    takeover = analyze_fraud(inputs((case, history), same_payer, team("owner-a"), team("owner-b")))
    assert not takeover.pending[0].established_exposure
    assert not takeover.contamination
    ring = seed("source", "owner-a", "implicated-card", evidence=case.disposition.reason)
    victim = historical(
        raw(
            "victim",
            team="owner-b",
            weeks=2,
            attempts=(tx("victim-attempt", "owner-b", "implicated-card", 2, DECLINED),),
        ),
        reason=ReasonCode.CARD_TESTING_VICTIM,
    )
    current = pending("action", "owner-b", tx("current", "owner-b", "different-card"))
    prepared = inputs(
        (ring, victim), current, team("owner-a", PHONE, DEVICE), team("owner-b", PHONE, DEVICE)
    )
    assert analyze_fraud(prepared).pending[0].status == Exposure.WEAK
    assert not analyze_fraud(prepared).contamination
    ach = pending(
        "ach",
        "owner-z",
        tx("ach-attempt", "owner-z", "implicated-card", method=PaymentMethod.ACH),
        tx("empty-attempt", "owner-z", " "),
    )
    missing = inputs((case,), ach, team("owner-a"), team("owner-z"))
    graph = build_graph(missing, resolve_seeds(missing)[0])
    assert not {
        node for node in graph if node.kind in {NodeKind.PHONE, NodeKind.BANK, NodeKind.DEVICE}
    }
    assert not [
        node
        for node in graph.neighbors(Node(NodeKind.TRANSACTION, "ach-attempt"))
        if node.kind == NodeKind.CARD
    ]
    assert Node(NodeKind.CARD, "") not in graph
    assert analyze_fraud(missing).pending[0].status == Exposure.INSUFFICIENT


@pytest.mark.parametrize("corroborated", [False, True])
def test_partial_bank_match_requires_corroboration_and_never_establishes_exposure(corroborated):
    case = seed(
        "source",
        "owner-a",
        "historic-card",
        reason=ATO,
        evidence=(
            "implicated transaction: source-tx\n"
            f"implicated bank routing and last4: {BANK}:0001\n"
            f"implicated signup device: {DEVICE}"
        ),
    )
    current = pending("action", "owner-b", tx("current", "owner-b", "different-card"))
    finding = analyze_fraud(
        inputs(
            (case,),
            current,
            team("owner-a", device=DEVICE, routing=BANK, last4="0001"),
            team(
                "owner-b",
                device=DEVICE if corroborated else "unrelated",
                routing=BANK,
                last4="0001",
            ),
        )
    ).pending[0]
    assert finding.status == (Exposure.UNRESOLVED if corroborated else Exposure.WEAK)
    assert not finding.established_exposure and finding.primary is None
    if corroborated:
        assert finding.leads[0].relationship == Relationship.UNRESOLVED_DESTINATION


def test_multiple_clusters_rank_by_strength_then_key():
    a = seed("first", "owner-a", "card-one")
    b = seed("second", "owner-b", "card-two")
    c = seed(
        "third",
        "owner-c",
        "card-three",
        evidence=(
            "implicated transaction: third-tx\n"
            f"implicated owner phone: {PHONE}\nimplicated signup device: {DEVICE}"
        ),
    )
    current = pending(
        "action",
        "owner-z",
        tx("match-one", "owner-z", "card-one"),
        tx("match-two", "owner-z", "card-two"),
    )
    prepared = inputs(
        (b, c, a), current, team("owner-c", PHONE, DEVICE), team("owner-z", PHONE, DEVICE)
    )
    analysis = analyze_fraud(prepared)
    finding = analysis.pending[0]
    direct_keys = sorted(
        cluster.stable_case_key
        for cluster in analysis.clusters
        if cluster.seed_payout_id != "third"
    )
    assert finding.primary.stable_case_key == direct_keys[0]
    assert finding.alternatives[0].stable_case_key == direct_keys[1]
    assert finding.alternatives[1].strength == Strength.CORROBORATED
    assert (
        analyze_fraud(
            replace(prepared, pending=(replace(current, attempts=current.attempts[::-1]),))
        )
        == analysis
    )


def test_mapping_is_optional_validated_versioned_and_sanitized():
    case = seed("source", "owner-a", "secret-card", evidence="Private ambiguous note")
    current = pending("action", "owner-b", tx("current", "owner-b", "secret-card"))
    prepared = inputs((case,), current)
    unconfirmed = analyze_fraud(prepared)
    assert not unconfirmed.clusters and unconfirmed.pending[0].status == Exposure.INSUFFICIENT
    mapped_case = MappedCase("source", ("source-tx",), FIRST_IMPORT)
    mapping = CaseMapping("local-v1", (mapped_case,))
    mapped = analyze_fraud(prepared, mapping)
    assert mapped.pending[0].status == Exposure.ESTABLISHED
    assert mapped.input_digest != unconfirmed.input_digest
    assert fraud_config(prepared.config, mapping).fraud_mapping_digest
    assert (
        analyze_fraud(prepared, replace(mapping, version="local-v2")).input_digest
        != mapped.input_digest
    )
    invalid = replace(mapping, cases=(replace(mapped_case, transaction_ids=("private-unknown",)),))
    with pytest.raises(ValueError, match="^invalid_fraud_case_mapping$"):
        analyze_fraud(prepared, invalid)
    future = replace(
        mapping, cases=(replace(mapped_case, available_at=LATER_IMPORT + timedelta(days=1)),)
    )
    with pytest.raises(ValueError, match="^invalid_fraud_case_mapping$"):
        analyze_fraud(prepared, future)
    bad_notes = replace(
        case, disposition=replace(case.disposition, reason="implicated team: unknown")
    )
    assert (
        analyze_fraud(replace(prepared, history=(bad_notes,))).unresolved_seeds[0].code
        == "invalid_case_reference"
    )


def test_contamination_recomputes_pending_baselines_without_future_leakage():
    old = historical(raw("old", weeks=7))
    captured = historical(
        raw("contaminated", weeks=4, attempts=(tx("old-match", "team-a", "case-card", 4),))
    )
    verified = historical(
        raw("victim", weeks=3, attempts=(tx("victim-tx", "team-a", "case-card", 3, DECLINED),)),
        reason=ReasonCode.CARD_TESTING_VICTIM,
    )
    subject = historical(raw("subject", weeks=1))
    fraud_seed = seed(
        "late-known", "owner-x", "case-card", weeks=5, decision=OBSERVED - timedelta(days=1)
    )
    current = pending("action", "team-a", tx("fresh", "team-a", "new-card"))
    prepared = inputs(
        (old, captured, verified, subject, fraud_seed),
        current,
        team("team-a", status=TeamStatus.SUSPENDED),
    )
    clean = analyze_fraud_and_statistics(
        replace(prepared, history=(old, captured, verified, subject)), POLICY
    )
    evaluated = analyze_fraud_and_statistics(prepared, POLICY)
    rows = {row.analysis.observation.payout_id: row for row in evaluated.statistics.history}
    assert rows["contaminated"].use == HistoryUse.EXCLUDED
    assert rows["victim"].use == HistoryUse.VALIDATION_ONLY
    assert rows["old"].use == HistoryUse.FIT_CANDIDATE
    assert rows["subject"].analysis.vector == next(
        row.analysis.vector
        for row in clean.statistics.history
        if row.analysis.observation.payout_id == "subject"
    )
    assert evaluated.statistics.pending[0].comparisons[0].team.baseline.payout_ids == (
        "old",
        "subject",
    )
    assert clean.statistics.pending[0].comparisons[0].team.baseline.payout_ids == (
        "contaminated",
        "old",
        "subject",
    )
    assert evaluated.fraud.pending[0].status == Exposure.NONE
    assert any(item.status == "anomaly" for item in evaluated.statistics.pending[0].findings)
    assert "action" not in rows


def test_card_payout_and_team_directives_require_a_verified_payment_anchor():
    note = "implicated payout: verified\nimplicated team: owner-a\nimplicated card: fixture-locked"
    valid = seed("verified", "owner-a", "fixture-locked", reason=TESTING, evidence=note)
    context_only = seed(
        "context",
        "owner-b",
        "not-implicated",
        evidence=("implicated payout: context\nimplicated team: owner-b"),
    )
    current = pending("action", "owner-c", tx("current", "owner-c", "fixture-locked"))
    prepared = inputs((valid, context_only), current)
    analysis = analyze_fraud(prepared)
    assert analysis.clusters[0].reason_code == TESTING
    assert analysis.pending[0].primary.supporting_seed_ids == ("verified",)
    assert analysis.unresolved_seeds[0].seed_payout_id == "context"
    wrong = replace(
        valid,
        disposition=replace(
            valid.disposition,
            reason=note.replace("implicated payout: verified", "implicated payout: action"),
        ),
    )
    assert not analyze_fraud(replace(prepared, history=(wrong,))).clusters


def test_mapping_review_availability_cannot_rewrite_an_earlier_vector():
    case = seed(
        "reviewed-source", "owner-x", "source-card", weeks=5, evidence="Needs local review."
    )
    older = historical(raw("normal", weeks=6))
    captured = historical(
        raw("old-capture", weeks=4, attempts=(tx("old-card", "team-a", "source-card", 4),))
    )
    subject = historical(raw("subject", weeks=1))
    action = pending("action", "team-a", tx("current", "team-a", "other-card"))
    prepared = inputs((case, older, captured, subject), action)
    prior = analyze_fraud_and_statistics(prepared, POLICY)
    mapping = CaseMapping(
        "reviewed-v1", (MappedCase("reviewed-source", ("reviewed-source-tx",), LATER_IMPORT),)
    )
    later = analyze_fraud_and_statistics(prepared, POLICY, mapping)
    assert later.fraud.contamination[0].exclusion.available_at == LATER_IMPORT
    original = {item.analysis.observation.payout_id: item for item in prior.statistics.history}
    after = {item.analysis.observation.payout_id: item for item in later.statistics.history}
    assert original["subject"].analysis.vector == after["subject"].analysis.vector
    assert after["old-capture"].use == HistoryUse.EXCLUDED
    assert later.statistics.pending[0].comparisons[0].team.baseline.payout_ids == (
        "normal",
        "subject",
    )
