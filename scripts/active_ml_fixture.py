"""Generate fictional joint-volume-and-tip cases with no application imports."""

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

from assessment_fixture import HEADERS, usd


def create(directory: Path, replay: bool) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    rows: dict[str, list] = {name: [] for name in HEADERS}
    lag = {0: 4, 1: 4, 3: 2, 4: 2}
    first = date(2026, 3, 2)
    published = [
        first + timedelta(days=offset)
        for offset in range(42)
        if (first + timedelta(days=offset)).weekday() in lag
    ]

    def activity(team, settlement, number, prefix, joint=False):
        day = settlement - timedelta(days=lag[settlement.weekday()])
        category = (number + team) % 4
        large = joint or category >= 2
        # Local service shops have wide ordinary price and tip ranges. Their
        # high-value contract jobs ordinarily have no percentage gratuity.
        band = (team - 1) % 6
        base = (2100 + 460 * band) * (3 if large else 1) + (category % 2) * 210
        high_tip = (48, 18, 55, 30, 65, 43)[band]
        tip_percent = high_tip if joint or not large else (2, 4, 6, 8, 10, 12)[band]
        count = 5 + category % 2
        total = 0
        source = (
            "transactions_10nov.csv" if prefix.startswith("NEXT") else "transactions_history.csv"
        )
        for index in range(count + 1):
            attempt = f"{prefix}-{index}"
            captured = index < count
            amount = base + 115 * index
            tip = amount * tip_percent // 100 if captured else 0
            method = "tap_to_pay" if index % 2 else "card"
            rows[source].append(
                (
                    attempt,
                    f"MODEL-T{team:02}",
                    f"{day} {9 + index:02}:17:00",
                    usd(amount),
                    usd(tip),
                    method,
                    f"MODEL-CARD-{attempt}",
                    "US",
                    "captured" if captured else "declined",
                    "Fictional buyer",
                    "00619",
                )
            )
            if captured:
                total += amount + tip
        return (prefix, f"MODEL-T{team:02}", f"{settlement} 17:30:00", day, day, count, usd(total))

    for team in range(1, 19):
        rows["teams.csv"].append(
            (
                f"MODEL-T{team:02}",
                f"Fictional shop {team:02}",
                "Plumbing",
                "Fictional owner",
                f"00055504{team:02}",
                "Fictional street",
                "Example Harbor",
                "MA",
                "2025-01-01",
                "active",
                "",
                "000000019",
                f"04{team:02}",
                f"MODEL-DEVICE-{team}",
            )
        )
        for number, settlement in enumerate(published):
            if replay and (number == 23 or (number == 22 and team == 3)):
                continue
            verified = number == len(published) - 2 and team in {1, 3, 5}
            payout = activity(team, settlement, number, f"TRAIN-{number:02}-{team:02}", verified)
            rows["payouts_history.csv"].append(
                (
                    *payout,
                    "MANUAL_APPROVED" if verified else "AUTO_APPROVED",
                    "Fixture reviewer",
                    f"{settlement} 18:30:00",
                    "OUTLIER_VERIFIED" if verified else "",
                    "Reviewed a fictional large contract with an exceptional gratuity."
                    if verified
                    else "",
                )
            )
    # The replay withholds a verified holdout observation and evaluates it at its
    # original settlement time. Later settlements are omitted from its history.
    pending = ((3, 22, True),) if replay else ((1, len(published), True), (2, len(published), False))
    for team, profile, joint in pending:
        payout = activity(
            team,
            published[22] if replay else date(2026, 4, 13),
            profile,
            f"NEXT-{team:02}",
            joint,
        )
        rows["payouts_pending.csv"].append((*payout, "PENDING_DECISION"))

    for name, items in rows.items():
        with (directory / name).open("x", encoding="utf8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS[name].split(","))
            writer.writerows(items)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    create(args.directory, args.replay)
