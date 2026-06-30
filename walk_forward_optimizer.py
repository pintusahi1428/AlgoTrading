import itertools
import json
import os
import time


PARAM_FILE = os.path.join("config", "optimized_params.json")
REPORT_FILE = os.path.join("logs", "walk_forward_report.json")
ALLOWED_BOUNDS = {
    "MIN_MATCH_PERCENT": (0.64, 0.74),
    "MIN_DIRECTION_LEAD": (3, 7),
}


def _load_trades(path=os.path.join("logs", "trade_journal.jsonl")):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
                row["pnl"] = float(row.get("pnl", 0.0))
                row["match_percent"] = float(row.get("match_percent", 0.0))
                row["direction_lead"] = int(row.get("direction_lead", 0))
                rows.append(row)
            except Exception:
                pass
    return rows


def _drawdown(pnls):
    equity = peak = worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _evaluate(rows, match_percent, direction_lead):
    selected = [row["pnl"] for row in rows if row["match_percent"] >= match_percent * 100.0 and row["direction_lead"] >= direction_lead]
    if not selected:
        return {"trades": 0, "score": -1e18, "pnl": 0.0, "expectancy": 0.0, "drawdown": 0.0}
    pnl = sum(selected)
    expectancy = pnl / len(selected)
    dd = _drawdown(selected)
    score = expectancy - (dd / max(1, len(selected)) * 0.30)
    return {"trades": len(selected), "score": score, "pnl": pnl, "expectancy": expectancy, "drawdown": dd}


def optimize_weekly(force=False):
    trades = _load_trades()
    minimum = int(os.getenv("WFO_MIN_TRADES", "40"))
    if len(trades) < minimum:
        return {"status": "INSUFFICIENT_DATA", "trades": len(trades), "required": minimum}
    if not force and os.path.exists(REPORT_FILE):
        age_days = (time.time() - os.path.getmtime(REPORT_FILE)) / 86400.0
        if age_days < float(os.getenv("WFO_UPDATE_DAYS", "7")):
            return {"status": "NOT_DUE", "age_days": round(age_days, 2)}

    match_grid = (0.64, 0.67, 0.70, 0.72, 0.74)
    lead_grid = (3, 4, 5, 6)
    fold_size = max(8, len(trades) // 4)
    candidates = []
    for match_percent, lead in itertools.product(match_grid, lead_grid):
        folds = []
        for end in range(fold_size * 2, len(trades) + 1, fold_size):
            validation = trades[end - fold_size:end]
            metric = _evaluate(validation, match_percent, lead)
            if metric["trades"] >= max(3, fold_size // 5):
                folds.append(metric)
        if len(folds) < 2:
            continue
        candidates.append({
            "match_percent": match_percent,
            "direction_lead": lead,
            "folds": len(folds),
            "score": sum(item["score"] for item in folds) / len(folds),
            "oos_trades": sum(item["trades"] for item in folds),
            "oos_pnl": sum(item["pnl"] for item in folds),
            "oos_drawdown": max(item["drawdown"] for item in folds),
        })
    if not candidates:
        return {"status": "NO_ROBUST_CANDIDATE", "trades": len(trades)}
    candidates.sort(key=lambda item: (item["score"], item["oos_pnl"], -item["oos_drawdown"]), reverse=True)
    best = candidates[0]
    params = {
        "MIN_MATCH_PERCENT": best["match_percent"],
        "MIN_DIRECTION_LEAD": best["direction_lead"],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_trades": len(trades),
    }
    os.makedirs("config", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    with open(PARAM_FILE, "w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=2)
    report = {"status": "UPDATED", "best": best, "top_candidates": candidates[:10], "params": params}
    with open(REPORT_FILE, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report


def apply_optimized_params():
    if not os.path.exists(PARAM_FILE):
        return {}
    try:
        with open(PARAM_FILE, "r", encoding="utf-8") as fh:
            params = json.load(fh)
        applied = {}
        for name, bounds in ALLOWED_BOUNDS.items():
            if name not in params:
                continue
            value = float(params[name])
            if not bounds[0] <= value <= bounds[1]:
                continue
            text_value = str(int(value)) if name == "MIN_DIRECTION_LEAD" else str(value)
            os.environ[name] = text_value
            applied[name] = text_value
        return applied
    except Exception:
        return {}


if __name__ == "__main__":
    print(json.dumps(optimize_weekly(force=True), indent=2))
