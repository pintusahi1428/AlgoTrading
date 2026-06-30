import json
import os
import tempfile
from datetime import datetime

from execution_monitor import ExecutionQualityMonitor
from regime_engine import RegimeDetectionEngine
from trade_analytics import analyze_trades
from walk_forward_optimizer import apply_optimized_params, optimize_weekly


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    engine = RegimeDetectionEngine()
    now = datetime(2026, 6, 29, 10, 30)
    trend = [100.0 + index for index in range(50)]
    result = engine.analyze(trend, now=now, previous_close=99.0)
    assert_true(result["day_type"] in ("TREND_DAY", "GAP_DAY"), "Trend/gap day regime detected")

    engine.reset()
    range_prices = [100.0 + (0.5 if index % 2 else -0.5) for index in range(60)]
    result = engine.analyze(range_prices, now=now, previous_close=99.5)
    assert_true(result["day_type"] == "RANGE_DAY", "Range day regime detected")

    engine.reset()
    gap_prices = [101.0 + index * 0.02 for index in range(40)]
    result = engine.analyze(gap_prices, now=now, previous_close=100.0)
    assert_true(result["day_type"] == "GAP_DAY" and result["gap_percent"] >= 0.35, "Gap day overlay detected")

    engine.reset()
    result = engine.analyze(trend, now=now, previous_close=100.0, expiry_date=now.date())
    assert_true(result["day_type"] == "EXPIRY_DAY", "Expiry day overlay detected")

    with tempfile.TemporaryDirectory() as temp_dir:
        monitor = ExecutionQualityMonitor(temp_dir)
        monitor.record_fill("NIFTYTESTCE", "BUY", "ENTRY", 100.0, 100.4, 1.0, 1.1, 1.2, 1.4, "1")
        monitor.record_missed("Risk gate", {"candidate_side": "BUY", "confidence": 30}, {"day_type": "TREND_DAY"})
        summary = monitor.summary()
        assert_true(summary["fills"] == 1 and summary["missed"] == 1, "Execution fills and missed trades recorded")

        journal = os.path.join(temp_dir, "trade_journal.jsonl")
        rows = [
            {"pnl": 100 if index % 3 else -60, "exit_reason": "BROKER_SL_FILLED", "mfe_points": 12, "matched_conditions": ["trend_direction", "vwap_direction"], "match_percent": 72, "direction_lead": 5}
            for index in range(45)
        ]
        with open(journal, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        os.makedirs(os.path.join(temp_dir, "logs"), exist_ok=True)
        with open(os.path.join(temp_dir, "logs", "trade_journal.jsonl"), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        analytics = analyze_trades(temp_dir)
        assert_true(analytics["sample_size"] == 45 and "trend_direction" in analytics["factor_attribution"], "Trade factor attribution generated")

        old_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            report = optimize_weekly(force=True)
            assert_true(report["status"] == "UPDATED", "Walk-forward optimizer updated bounded parameters")
            applied = apply_optimized_params()
            assert_true("MIN_MATCH_PERCENT" in applied and "MIN_DIRECTION_LEAD" in applied, "Optimized parameters applied from approved bounds")
        finally:
            os.chdir(old_cwd)

    print("\nADVANCED ENGINE TEST RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
