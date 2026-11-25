"""Generate fictional CSVs for the assessment calendar, without application imports."""

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

PROFESSIONS = (
    "Plumbing",
    "Locksmith",
    "HVAC",
    "Electrical",
    "Garage Door",
    "Appliance Repair",
    "Pest Control",
    "Handyman",
)
HEADERS = {
    "teams.csv": (
        "team_id,team_name,profession,owner_name,owner_phone,business_address,city,state,"
        "onboarded_on,status,suspended_on,payout_bank_routing,payout_bank_last4,signup_device_id"
    ),
    "transactions_history.csv": (
        "txn_id,team_id,created_at_ist,amount_usd,tip_usd,payment_method,card_fingerprint,"
        "card_country,status,customer_name,customer_zip"
    ),
    "payouts_history.csv": (
        "payout_id,team_id,created_at_ist,covers_from,covers_to,txn_count,payout_amount_usd,"
        "decision,decided_by,decided_at_ist,reason_code,reason"
    ),
    "payouts_pending.csv": (
        "payout_id,team_id,created_at_ist,covers_from,covers_to,txn_count,payout_amount_usd,status"
    ),
}
HEADERS["transactions_10nov.csv"] = HEADERS["transactions_history.csv"]

# Handwritten calendar oracle: settlement date -> inclusive collection window.
WINDOWS = (
    (10, 23, 10, 21, 1),
    (10, 26, 10, 22, 1),
    (10, 27, 10, 23, 1),
    (10, 28, 10, 24, 3),
    (10, 29, 10, 27, 1),
    (10, 30, 10, 28, 1),
    (11, 2, 10, 29, 1),
    (11, 3, 10, 30, 1),
    (11, 4, 10, 31, 3),
    (11, 5, 11, 3, 1),
    (11, 6, 11, 4, 1),
    (11, 9, 11, 5, 1),
    (11, 10, 11, 6, 1),
    (11, 11, 11, 7, 3),
)
SEEDS = {
    1: "FRAUD_CARD_RING",
    2: "FRAUD_CARD_TESTING",
    3: "FRAUD_ACCOUNT_TAKEOVER",
}


def usd(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02}"


def attempts(prefix: str, team: int, day: date, amounts: tuple[int, int], card: str = ""):
    business = f"{team:04}"
    return [
        (
            f"{prefix}-1",
            business,
            f"{day} 00:00:00",
            usd(amounts[0]),
            "1.25",
            "card",
            card or f"CARD-{prefix}",
            "US",
            "captured",
            "Fictional, buyer",
            "00420",
        ),
        (
            f"{prefix}-2",
            business,
            f"{day} 13:05:00",
            usd(amounts[1]),
            "0.75",
            "tap_to_pay",
            f"TAP-{prefix}",
            "US",
            "captured",
            "Fictional buyer",
            "00012",
        ),
        (
            f"{prefix}-3",
            business,
            f"{day} 12:02:00",
            "12.00",
            "0.00",
            "card",
            f"DECLINE-{prefix}",
            "US",
            "declined",
            "Fictional buyer",
            "00331",
        ),
        (
            f"{prefix}-4",
            business,
            f"{day} 18:02:00",
            "15.00",
            "0.00",
            "ach",
            "",
            "",
            "refunded",
            "Fictional buyer",
            "00511",
        ),
    ]


def write_fixture(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list] = {name: [] for name in HEADERS}
    for team in range(1, 25):
        business = f"{team:04}"
        rows["teams.csv"].append(
            (
                business,
                f"Fictional workshop {team:02}",
                PROFESSIONS[(team - 1) % 8],
                f"Fictional owner {team:02}",
                f"00055501{team:02}",
                f"{team} Example Road",
                "Example Harbor",
                "MA",
                "2024-06-01",
                "suspended" if team == 24 else "active",
                "2026-11-12" if team == 24 else "",
                "000000019",
                business,
                f"DEVICE-{2 if team == 6 else team}",
            )
        )
        for wave, (month, day, start_month, start_day, duration) in enumerate(WINDOWS):
            payout_date = date(2026, month, day)
            start = date(2026, start_month, start_day)
            end = start + timedelta(days=duration - 1)
            prefix = f"H-{wave:02}-{team:02}"
            base = 5700 + team * 15 + (wave % 4) * 100
            reason_code = SEEDS.get(team, "") if wave == 1 else ""
            decision, reason = "AUTO_APPROVED", ""
            if reason_code:
                decision, reason = "REJECTED", f"implicated transaction: {prefix}-1"
            elif wave == 10 and team in {7, 8}:
                decision = "MANUAL_APPROVED"
                reason_code = "OUTLIER_VERIFIED" if team == 7 else "CARD_TESTING_VICTIM"
                reason = "Fictional invoice and captured payments verified."
            count = total = 0
            for offset in range(duration):
                # The verified invoice is for an exceptional fictional service,
                # while the victim's captured proceeds stay consistent with its history.
                amounts = (
                    (46000, 17000)
                    if reason_code == "OUTLIER_VERIFIED"
                    else (base, 3100 + team * 12 + (wave % 3) * 50)
                )
                rows["transactions_history.csv"].extend(
                    attempts(
                        prefix if offset == 0 else f"{prefix}-D{offset}",
                        team,
                        start + timedelta(days=offset),
                        amounts,
                        f"SEED-CARD-{team}" if reason_code in SEEDS.values() else "",
                    )
                )
                count += 2
                total += sum(amounts) + 200
            rows["payouts_history.csv"].append(
                (
                    prefix,
                    business,
                    f"{payout_date} 17:30:00",
                    start,
                    end,
                    count,
                    usd(total),
                    decision,
                    "Fictional reviewer",
                    f"{payout_date} 18:00:00",
                    reason_code,
                    reason,
                )
            )
        if team > 20:
            continue
        base = 30000 if team == 4 else 5800 + team * 15
        amounts = base, 3150 + team * 12
        current = attempts(
            f"CURRENT-{team:02}",
            team,
            date(2026, 11, 10),
            amounts,
            "SEED-CARD-1" if team == 1 else "",
        )
        if team in {2, 3}:
            # Unsuccessful and refunded links do not establish exposure of payable funds.
            changed = list(current[team])
            changed[5:8] = ["card", f"SEED-CARD-{team}", "US"]
            current[team] = tuple(changed)
        rows["transactions_10nov.csv"].extend(current)
        rows["payouts_pending.csv"].append(
            (
                f"P-{team:02}",
                business,
                "2026-11-12 17:30:00",
                "2026-11-10",
                "2026-11-10",
                2,
                usd(sum(amounts) + 200),
                "PENDING_DECISION",
            )
        )
    # Creation fails if any destination already exists.
    if any((directory / name).exists() for name in rows):
        raise FileExistsError("Choose a new fixture directory.")
    for name, content in rows.items():
        with (directory / name).open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS[name].split(","))
            writer.writerows(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    write_fixture(parser.parse_args().directory)
