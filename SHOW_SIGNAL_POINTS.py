import json
import os
import sys


REPORT = os.path.join("logs", "last_signal_points.json")


def print_direction(label, score, available, rows):
    print("-" * 88)
    print(f"{label}: {score}/{available}")
    print(f"{'CONDITION':<32} {'OBSERVED':<18} {'STATUS':<12} {'POINT':>5} {'CRITICAL':>9}")
    print("-" * 88)
    for row in rows:
        print(
            f"{row.get('condition', ''):<32} {str(row.get('observed', '')):<18} "
            f"{row.get('status', ''):<12} {row.get('earned_points', 0):>2}/{row.get('max_points', 1):<2} "
            f"{str(bool(row.get('critical'))):>9}"
        )


def main():
    if not os.path.exists(REPORT):
        print("No live score report yet. Run bot.py during market hours until warm-up completes.")
        return 1
    with open(REPORT, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    print("=" * 88)
    print(f"MASTER SNIPER CONDITION POINT REPORT | {report.get('timestamp', '')}")
    print(f"Signal: {report.get('signal')} | Candidate: {report.get('candidate')} | Required: {report.get('required')}")
    print_direction("CE BUY", report.get("buy_score", 0), report.get("buy_available", 0), report.get("buy_conditions", []))
    print_direction("PE BUY", report.get("sell_score", 0), report.get("sell_available", 0), report.get("sell_conditions", []))
    missing = report.get("missing_live_factors", [])
    if missing:
        print("-" * 88)
        print("Unavailable live factors (zero points, no dummy replacement):")
        for item in missing:
            print(f"  - {item}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
