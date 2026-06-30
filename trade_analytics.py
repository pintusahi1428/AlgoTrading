import json
import os
from collections import defaultdict


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _max_drawdown(pnls):
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _reason(row):
    pnl = float(row.get("pnl", 0.0))
    mfe = float(row.get("mfe_points", 0.0))
    reason = str(row.get("exit_reason", "UNKNOWN"))
    if pnl >= 0:
        if reason in ("BROKER_SL_FILLED", "LOCAL_TRAILING_SL", "BROKER_POSITION_FLAT"):
            return "TRAILING_PROFIT_CAPTURE"
        if reason == "DAILY_EQUITY_SHIELD":
            return "DAILY_PROFIT_PROTECTION"
        return "DIRECTIONAL_EDGE_CAPTURE"
    if mfe > 5.0:
        return "PROFIT_GIVEBACK_BEFORE_EXIT"
    if reason == "TIME_SQUARE_OFF":
        return "LATE_SESSION_UNRESOLVED"
    return "SIGNAL_FAILED_OR_INITIAL_STOP"


def analyze_trades(log_dir="logs"):
    path = os.path.join(log_dir, "trade_journal.jsonl")
    rows = _load_jsonl(path)
    report = {"sample_size": len(rows), "status": "INSUFFICIENT_DATA", "factor_attribution": {}}
    if not rows:
        return report
    pnls = [float(row.get("pnl", 0.0)) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    reasons = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    factors = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for row, pnl in zip(rows, pnls):
        reason = _reason(row)
        reasons[reason]["count"] += 1
        reasons[reason]["pnl"] += pnl
        for factor in row.get("matched_conditions", []) or []:
            factors[str(factor)]["count"] += 1
            factors[str(factor)]["wins"] += int(pnl > 0)
            factors[str(factor)]["pnl"] += pnl
    factor_report = {}
    for name, data in factors.items():
        factor_report[name] = {
            "trades": data["count"],
            "win_rate": round(data["wins"] / data["count"] * 100.0, 2) if data["count"] else 0.0,
            "total_pnl": round(data["pnl"], 2),
            "avg_pnl": round(data["pnl"] / data["count"], 2) if data["count"] else 0.0,
        }
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (9.99 if gross_win > 0 else 0.0)
    expectancy = sum(pnls) / len(pnls)
    drawdown = _max_drawdown(pnls)
    if len(rows) < 30:
        rating = "INSUFFICIENT_DATA"
    elif expectancy > 0 and profit_factor >= 1.5 and drawdown <= max(gross_win * 0.35, 1.0):
        rating = "A"
    elif expectancy > 0 and profit_factor >= 1.2:
        rating = "B"
    elif expectancy > 0:
        rating = "C"
    else:
        rating = "D"
    report = {
        "sample_size": len(rows),
        "status": "READY" if len(rows) >= 30 else "INSUFFICIENT_DATA",
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(rows) * 100.0, 2),
        "total_pnl": round(sum(pnls), 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 3),
        "max_drawdown": round(drawdown, 2),
        "win_loss_reasons": {name: {"count": data["count"], "pnl": round(data["pnl"], 2)} for name, data in reasons.items()},
        "factor_attribution": factor_report,
        "final_rating": rating,
    }
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "trade_analytics.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report


def print_report(report):
    print("=" * 88)
    print("MASTER SNIPER TRADE ANALYTICS")
    print("=" * 88)
    print(f"Trades: {report.get('sample_size', 0)} | Status: {report.get('status')} | Rating: {report.get('final_rating', 'NO_DATA')}")
    print(f"Win rate: {report.get('win_rate', 0.0):.2f}% | PnL: {report.get('total_pnl', 0.0):.2f} | Expectancy: {report.get('expectancy', 0.0):.2f}")
    print(f"Profit factor: {report.get('profit_factor', 0.0)} | Max drawdown: {report.get('max_drawdown', 0.0):.2f}")
    print("-" * 88)
    print("WIN/LOSS REASONS")
    for name, data in sorted(report.get("win_loss_reasons", {}).items()):
        print(f"{name:<36} count={data['count']:<4} pnl={data['pnl']:>10.2f}")
    print("-" * 88)
    print("TOP FACTOR ATTRIBUTION")
    ranked = sorted(report.get("factor_attribution", {}).items(), key=lambda item: item[1]["avg_pnl"], reverse=True)
    for name, data in ranked[:20]:
        print(f"{name:<30} trades={data['trades']:<4} win={data['win_rate']:>6.2f}% avg={data['avg_pnl']:>9.2f}")
    print("=" * 88)


if __name__ == "__main__":
    print_report(analyze_trades())
