import csv
import os
from collections import Counter
from datetime import datetime

os.environ["MIN_AVAILABLE_CONDITIONS"] = os.getenv("BACKTEST_MIN_AVAILABLE_CONDITIONS", "10")
os.environ["MIN_SIGNAL_SCORE"] = os.getenv("BACKTEST_MIN_SIGNAL_SCORE", "8")
os.environ["MIN_MATCH_PERCENT"] = os.getenv("BACKTEST_MIN_MATCH_PERCENT", "0.70")
os.environ["MIN_DIRECTION_LEAD"] = os.getenv("BACKTEST_MIN_DIRECTION_LEAD", "3")
os.environ["MIN_CRITICAL_MATCHES"] = os.getenv("BACKTEST_MIN_CRITICAL_MATCHES", "3")

from adaptive_engine import get_market_regime
from regime_engine import RegimeDetectionEngine
from smart_money import directional_smc_score
from strategy import calculate_real_vwap, get_signal, reset_daily_session


DATA_FILE = os.getenv("BACKTEST_DATA", "historical_data.csv")


def read_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                yield {
                    "timestamp": row["timestamp"],
                    "close": float(row["close"]),
                    "volume": float(row.get("volume") or 1),
                }
            except Exception:
                continue


def main():
    if not os.path.exists(DATA_FILE):
        raise SystemExit(f"Historical file not found: {DATA_FILE}")

    reset_daily_session()
    history = []
    signals = Counter()
    regimes = Counter()
    total = 0
    max_confidence = 0
    active_date = None
    previous_close = None
    last_price = None
    day_engine = RegimeDetectionEngine()
    day_types = Counter()

    for row in read_rows(DATA_FILE):
        total += 1
        row_date = row["timestamp"][:10]
        if row_date != active_date:
            previous_close = last_price
            reset_daily_session()
            day_engine.reset()
            history = []
            active_date = row_date
        price = row["close"]
        history.append(price)
        history = history[-90:]
        vwap = calculate_real_vwap("NIFTY", price, row["volume"])
        regime = get_market_regime(history)
        regimes[regime] += 1
        action = "BUY" if price > vwap else "SELL"
        if len(history) >= 16:
            one = "BULLISH" if price > history[-2] else "BEARISH"
            five = "BULLISH" if price > history[-6] else "BEARISH"
            fifteen = "BULLISH" if price > history[-16] else "BEARISH"
            mtf = "STRONG_" + one if one == five == fifteen else "MIXED_CHOPS"
        else:
            mtf = "NEUTRAL"
        smc_value, smc_direction, smc_data = directional_smc_score(history)
        day_regime = day_engine.analyze(
            history,
            now=datetime.fromisoformat(row["timestamp"]),
            previous_close=previous_close,
        )
        day_types[day_regime["day_type"]] += 1
        inputs = {
            "action": action,
            "smc_score": smc_value,
            "smc_direction": smc_direction,
            "smc_direction_available": smc_direction != "NEUTRAL",
            "trend": regime,
            "trend_direction": "BULLISH" if len(history) >= 8 and price > history[-8] else "BEARISH",
            "trend_direction_available": len(history) >= 8,
            "momentum": 1 if len(history) > 1 and price > history[-2] else -1,
            "index_sync": False,
            "index_sync_available": False,
            "vwap_dist": abs(price - vwap),
            "vwap_bounce": "RETEST_ZONE" if abs(price - vwap) <= 12.0 else "AWAY_FROM_VWAP",
            "vwap_direction": "BULLISH" if price > vwap else "BEARISH",
            "vwap_direction_available": True,
            "prices_history": history,
            "mtf_confluence": mtf,
            "fvg_signal": smc_data.get("fvg") or "NONE",
            "fvg_available": smc_data.get("fvg") is not None,
            "strong_sweep": smc_data.get("strong_sweep") or "NONE",
            "strong_sweep_available": smc_data.get("strong_sweep") is not None,
            "oi_available": False,
            "chain_available": False,
            "iv_available": False,
        }
        inputs.update(day_regime)
        signal = get_signal(inputs)
        max_confidence = max(max_confidence, int(signal.get("confidence", 0)))
        signals[signal.get("side", "HOLD")] += 1
        last_price = price

    print("=" * 70)
    print("MASTER SNIPER SIGNAL REPLAY")
    print("=" * 70)
    print(f"Candles processed : {total}")
    print(f"BUY signals       : {signals['BUY']}")
    print(f"SELL signals      : {signals['SELL']}")
    print(f"HOLD candles      : {signals['HOLD']}")
    print(f"Maximum score     : {max_confidence}")
    print("Regimes:")
    for name, count in regimes.most_common():
        print(f"  {name:<20} {count}")
    print("Day types:")
    for name, count in day_types.most_common():
        print(f"  {name:<20} {count}")
    print("=" * 70)
    print("NOTE: This validates historical-only direction logic with reduced data-coverage gates.")
    print("Actual option PnL requires historical option premium, IV, OI and chain data.")
    if signals["BUY"] + signals["SELL"] == 0:
        raise SystemExit("FAILED: Strategy produced zero actionable signals.")


if __name__ == "__main__":
    main()
