"""Validate a local assessment run and downloaded export without sending source files."""

import argparse
import csv
import json
import re
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from getpass import GetPassWarning, getpass
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from zoneinfo import ZoneInfo

from assessment_fixture import HEADERS

EXPORT_COLUMNS = ["payout_id", "team_id", "decision", "fraud_cluster", "reasons", "evidence"]
LABEL = {"APPROVE": "Approve", "MANUAL_REVIEW": "Manual review", "REJECT": "Reject"}
IST = ZoneInfo("Asia/Kolkata")
CUTOFF = datetime(2026, 11, 13, 10, tzinfo=IST)


class VerificationError(ValueError):
    """Safe, fixed acceptance message."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_rows(path: Path, columns=None):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if columns is not None:
            check(
                reader.fieldnames == columns, "CSV column names or order differ from the contract."
            )
        return list(reader)


def ist(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    return timestamp.astimezone(IST) if timestamp.tzinfo else timestamp.replace(tzinfo=IST)


def cents(value: str) -> int:
    number = Decimal(value) * 100
    check(
        number.is_finite() and number == number.to_integral_value(), "Invalid currency precision."
    )
    return int(number)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        return None


class LocalAPI:
    def __init__(self, origin: str):
        parsed = urlsplit(origin)
        check(
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port
            and origin == f"http://127.0.0.1:{parsed.port}",
            "Use an explicit loopback HTTP origin.",
        )
        self.origin = origin
        self.client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), NoRedirect())

    def call(self, path: str, values: dict | None = None):
        headers = {"Accept": "application/json"}
        data = None
        if values is not None:
            csrf = self.call("/api/auth/csrf")["csrf_token"]
            headers.update(
                {"Origin": self.origin, "X-CSRF-Token": csrf, "Content-Type": "application/json"}
            )
            data = json.dumps(values).encode()
        with self.client.open(Request(self.origin + path, data, headers), timeout=15) as response:
            return json.load(response) if response.status != 204 else None


def verify(api, history: Path, action: Path, run_id: str, exported: Path):
    teams, settled, old_tx, current_tx, pending = (
        read_rows(directory / name, HEADERS[name].split(","))
        for directory, name in (
            (history, "teams.csv"),
            (history, "payouts_history.csv"),
            (history, "transactions_history.csv"),
            (action, "transactions_10nov.csv"),
            (action, "payouts_pending.csv"),
        )
    )
    check(len(teams) == len({row["team_id"] for row in teams}) == 24, "Expected 24 distinct teams.")
    pending_by_id = {row["payout_id"]: row for row in pending}
    check(len(pending) == len(pending_by_id) == 20, "Expected 20 distinct pending payouts.")
    check(
        len({row["team_id"] for row in pending}) == 20,
        "Expected one pending payout per team on the assessment settlement date.",
    )
    comparison_day = date(2026, 11, 12)
    collection_day = date(2026, 11, 10)
    check(
        all(ist(row["created_at_ist"]).date() == collection_day for row in current_tx),
        "Action-day transactions must be dated November 10 IST.",
    )
    historical_days = [ist(row["created_at_ist"]).date() for row in old_tx]
    check(
        historical_days
        and min(historical_days) == date(2026, 10, 21)
        and max(historical_days) == date(2026, 11, 9),
        "Historical collection date bounds differ.",
    )
    daily = defaultdict(lambda: defaultdict(lambda: {"count": 0, "cents": 0}))
    for row in settled:
        day = ist(row["created_at_ist"]).date()
        if ist(row["decided_at_ist"]) <= CUTOFF:
            total = daily[day][row["decision"]]
            total["count"] += 1
            total["cents"] += cents(row["payout_amount_usd"])
    check(
        daily and min(daily) == date(2026, 10, 23) and max(daily) == date(2026, 11, 11),
        "Known historical settlement date bounds differ.",
    )

    query = "?" + urlencode({"run_id": run_id})
    run = api.call("/api/evaluations/" + quote(run_id, safe=""))
    dashboard = api.call("/api/dashboard" + query)
    check(
        run["status"] == "COMPLETED" and ist(run["cutoff_at"]) == CUTOFF,
        "Expected a completed run with the assessment cutoff.",
    )
    check(
        run["model"] is not None and dashboard["ml"] is not None,
        "Published ML metadata is missing.",
    )
    model = run["model"]
    # The approved training pool bounds both partitions. Exact member eligibility
    # still requires the targeted regression tests and a private manifest review.
    approvals = sum(
        row["decision"] in {"AUTO_APPROVED", "MANUAL_APPROVED"}
        and ist(row["decided_at_ist"]) <= CUTOFF
        for row in settled
    )
    check(
        model["training_observation_count"] + model["validation_observation_count"] <= approvals,
        "Reported training and validation counts exceed known approved history.",
    )
    historical = dashboard["historical"]
    check(
        historical["calendar_days"] == 20 and len(historical["days"]) == 20,
        "Expected exactly 20 calendar days of historical comparison.",
    )
    for offset, row in enumerate(historical["days"]):
        day = comparison_day - timedelta(days=20 - offset)
        check(row["date_ist"] == day.isoformat(), "Historical payout date does not match.")
        expected = daily.get(day)
        for category in ("AUTO_APPROVED", "MANUAL_APPROVED", "REJECTED"):
            observed = (row["totals"] or {}).get(category, {"count": 0, "cents": 0})
            check(
                observed == (expected or {}).get(category, {"count": 0, "cents": 0}),
                "Historical count or cents differ from source dispositions.",
            )
        if expected is None:
            check(
                (row["totals"] is None) == (day.weekday() < 5),
                "Missing weekday coverage and recorded zero activity were conflated.",
            )

    export_rows = read_rows(exported, EXPORT_COLUMNS)
    check(
        len(export_rows) == 20
        and {row["payout_id"] for row in export_rows} == pending_by_id.keys(),
        "Export must contain the exact 20 pending payout IDs.",
    )
    check(
        [row["payout_id"] for row in export_rows] == sorted(pending_by_id),
        "Export row ordering differs.",
    )
    source_ids = {
        "team": {row["team_id"] for row in teams},
        "payout": {row["payout_id"] for row in settled + pending},
        "transaction": {row["txn_id"] for row in old_tx + current_tx},
    }
    clusters = {row["cluster_id"]: row for row in api.call("/api/fraud-clusters" + query)}
    fraud_codes = {"FRAUD_CARD_RING", "FRAUD_CARD_TESTING", "FRAUD_ACCOUNT_TAKEOVER"}
    cases = {
        row["payout_id"]: row
        for row in settled
        if row["decision"] == "REJECTED" and row["reason_code"] in fraud_codes
    }
    published = {
        item["id"]
        for cluster in clusters.values()
        for item in cluster["evidence"]
        if item["type"] == "payout"
    }
    validation_codes = {"OUTLIER_VERIFIED", "VOLUME_SPIKE_VERIFIED", "CARD_TESTING_VICTIM"}
    for code in sorted(validation_codes):
        verified = sum(
            row["reason_code"] == code
            and row["decision"] in {"AUTO_APPROVED", "MANUAL_APPROVED"}
            for row in settled
        )
        print(f"{code}: {verified} verified historical approvals supplied for context")
    for code in sorted(fraud_codes):
        ids = {key for key, case in cases.items() if case["reason_code"] == code}
        print(
            f"{code}: {len(ids)} supplied cases, {len(ids & published)} published seeds"
        )
        for key in sorted(ids - published):
            print(
                f"  {key}: no published seed in this run; verify cutoff and case evidence"
            )
    for key, cluster in sorted(clusters.items()):
        sources = sorted(f"{ref['type']}:{ref['id']}" for ref in cluster["evidence"])
        print(f"Published cluster {key}: {', '.join(sources)}")
    totals = {
        name: {"count": 0, "cents": 0}
        for name in (
            "automatically_approved",
            "analyst_approved",
            "open_manual_review",
            "rejected",
        )
    }
    for row in export_rows:
        source = pending_by_id[row["payout_id"]]
        check(
            ist(source["created_at_ist"]) == comparison_day_at_close()
            and source["covers_from"] == source["covers_to"] == collection_day.isoformat(),
            "Pending settlement timing or coverage differs.",
        )
        captured = [
            tx
            for tx in current_tx
            if tx["team_id"] == source["team_id"]
            and source["covers_from"]
            <= ist(tx["created_at_ist"]).date().isoformat()
            <= source["covers_to"]
            and tx["status"] == "captured"
        ]
        payable = sum(cents(tx["amount_usd"]) + cents(tx["tip_usd"]) for tx in captured)
        check(
            payable == cents(source["payout_amount_usd"])
            and len(captured) == int(source["txn_count"]),
            "Pending captured count or cents do not reconcile.",
        )
        detail = api.call("/api/payouts/" + quote(row["payout_id"], safe="") + query)
        payout, active = detail["payout"], detail["decisions"]["effective"]
        check(
            row["team_id"] == source["team_id"] == payout["team_id"]
            and payout["payout_amount_cents"] == payable
            and payout["captured_count"] == len(captured),
            "Published payout team, count or cents differ from independent reconciliation.",
        )
        decision = active["effective_decision"]
        check(row["decision"] == LABEL[decision], "CSV decision differs from effective decision.")
        reason = active["reason"]
        trimmed = reason.lstrip("".join(chr(code) for code in range(33)) + "\ufeff")
        formula = reason.startswith(("\t", "\r", "\n")) or trimmed.lstrip().startswith(
            ("=", "+", "-", "@", "＝", "＋", "＠", "－")
        )
        expected_reason = "'" + reason if formula else reason
        check(
            row["reasons"] == expected_reason and reason.strip(),
            "CSV reason differs from signed-off evidence.",
        )
        ids = {item["id"] for item in active["evidence"]}
        check(
            all(item["id"] in source_ids.get(item["type"], set()) for item in active["evidence"]),
            "Evidence references a record outside the original source files.",
        )
        check(
            row["evidence"] == ";".join(sorted(ids)),
            "CSV evidence differs from effective evidence.",
        )
        check(decision == "APPROVE" or bool(ids), "Review or Reject is missing evidence.")
        covered_ids = {item["txn_id"] for item in detail["transactions"]}
        check(
            decision == "APPROVE" or bool(ids & (covered_ids | {row["payout_id"], row["team_id"]})),
            "Review or Reject has no reference to the current payout, team, or covered attempts.",
        )
        if decision == "REJECT":
            cluster = clusters.get(active["cluster_id"])
            check(
                cluster is not None
                and row["fraud_cluster"] == cluster["cluster_id"]
                and any(
                    link["payout_id"] == row["payout_id"]
                    and any(
                        step["supports_exposure"]
                        for path in link["paths"]
                        for step in path["steps"]
                    )
                    for link in cluster["associated_payouts"]
                ),
                "Reject lacks a same-run cluster and published path.",
            )
        else:
            check(
                not row["fraud_cluster"] and active["cluster_id"] is None,
                "Non-Reject references a fraud cluster.",
            )
        fraud = detail.get("fraud") or {}
        fraud_status = fraud.get("status", "unavailable")
        anomalies = sorted({
            (comparison["feature"], scope)
            for pair in detail.get("statistics", ())
            for scope in ("team", "profession")
            if (comparison := pair.get(scope)) and comparison["status"] == "anomaly"
        })
        ml = detail.get("ml") or {}
        print(
            f"{row['payout_id']}: {decision}, fraud={fraud_status}, evidence={row['evidence']}"
        )
        print(f"  Review signals: statistics={anomalies}, ML={ml.get('status', 'unavailable')}, "
              f"actionable ML={ml.get('qualifies_for_review', False)}, "
              f"known payable exposure={fraud.get('established_exposure', False)}")
        for lead in fraud.get("leads", ()):
            print(
                f"  Verify {lead['relationship']} against historical payout "
                f"{lead['seed_payout_id']} before inferring exposure."
            )
        category = {"MANUAL_REVIEW": "open_manual_review", "REJECT": "rejected"}.get(decision)
        if category is None:
            category = (
                "analyst_approved"
                if active["decision_source"] == "HUMAN"
                else "automatically_approved"
            )
        totals[category]["count"] += 1
        totals[category]["cents"] += payable
    check(
        dashboard["categories"] == totals, "Dashboard category amounts or counts do not reconcile."
    )
    amount = sum(bucket["cents"] for bucket in totals.values())
    check(
        dashboard["total"] == {"count": 20, "cents": amount},
        "Dashboard grand total does not reconcile.",
    )
    return amount, model["status"]


def comparison_day_at_close() -> datetime:
    return datetime(2026, 11, 12, 17, 30, tzinfo=IST)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", required=True, type=Path)
    parser.add_argument("--action-dir", required=True, type=Path)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--origin", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    try:
        check(bool(re.fullmatch(r"[0-9a-fA-F-]{36}", args.run_id)), "Use a completed run UUID.")
        api = LocalAPI(args.origin)
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("Password: ")
        account = api.call("/api/auth/login", {"username": args.username, "password": password})
        check(account["mode"] == "local", "Run this private verification in local mode.")
        amount, model_status = verify(
            api, args.history_dir, args.action_dir, args.run_id, args.export
        )
    except VerificationError as exc:
        parser.exit(1, f"Verification stopped. {exc}\n")
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        parser.exit(1, "Verification stopped. A hidden interactive password prompt is required.\n")
    except (URLError, OSError, csv.Error, ArithmeticError, KeyError, TypeError, ValueError):
        parser.exit(1, "Verification stopped. Check the files, local session and API contract.\n")
    print(
        f"Verified 20 payouts, {amount} payable cents, 20 historical dates and the downloaded CSV."
    )
    print(
        f"Published ML status is {model_status}. "
        "Review fraud evidence and training eligibility privately."
    )


if __name__ == "__main__":
    main()
