import os

os.environ["MIN_AVAILABLE_CONDITIONS"] = "24"
os.environ["MIN_SIGNAL_SCORE"] = "18"
os.environ["MIN_MATCH_PERCENT"] = "0.67"
os.environ["MIN_DIRECTION_LEAD"] = "4"
os.environ["MIN_CRITICAL_MATCHES"] = "5"
os.environ["MAX_CRITICAL_CONFLICTS"] = "2"

from strategy import get_signal


def assert_side(payload, expected, message):
    result = get_signal(payload)
    if result["side"] != expected:
        raise AssertionError(f"{message}: expected {expected}, got {result}")
    print(f"PASS: {message} ({result['confidence']}/{result['available']}, lead {result['direction_lead']})")
    return result


def payload(direction):
    bullish = direction == "BULLISH"
    action = "BUY" if bullish else "SELL"
    return {
        "action": action,
        "prices_history": [100 + (i if bullish else -i) for i in range(60)],
        "trend": "STRONG_TREND",
        "day_type": "TREND_DAY",
        "day_type_direction_available": True,
        "day_type_direction": direction,
        "regime_stability_available": True,
        "regime_stability": 0.75,
        "day_threshold_adjustment": 0,
        "trend_direction_available": True,
        "trend_direction": direction,
        "iv_available": True,
        "iv_percentile": 25,
        "india_vix_available": True,
        "india_vix": 16,
        "index_sync_available": True,
        "index_sync_direction": direction,
        "top_weighted_available": True,
        "top_weighted_confirmation": direction,
        "top15_weighted_available": True,
        "top15_weighted_confirmation": direction,
        "banknifty_vwap_available": True,
        "banknifty_vwap_confirmation": direction,
        "banknifty_mtf_available": True,
        "banknifty_mtf_confirmation": "STRONG_" + direction,
        "momentum": 1 if bullish else -1,
        "smc_score": 85,
        "smc_direction_available": True,
        "smc_direction": direction,
        "fvg_available": True,
        "fvg_signal": ("BULLISH" if bullish else "BEARISH") + "_FVG",
        "strong_sweep_available": True,
        "strong_sweep": "STRONG_SELL_SIDE_SWEEP" if bullish else "STRONG_BUY_SIDE_SWEEP",
        "smc_volume_confirmation_available": True,
        "smc_volume_confirmation": "CONFIRMED",
        "mtf_confluence": "STRONG_" + direction,
        "oi_available": True,
        "oi_buildup": "LONG_BUILDUP" if bullish else "SHORT_BUILDUP",
        "coi_available": True,
        "coi_signal": "CALL_COI_BULLISH" if bullish else "PUT_COI_BEARISH",
        "coi_imbalance_available": True,
        "coi_imbalance": 1.4 if bullish else 0.65,
        "greeks_available": True,
        "greeks_bias": direction,
        "dealer_position_available": True,
        "dealer_position_signal": "DEALER_SUPPORT" if bullish else "DEALER_RESISTANCE",
        "max_pain_available": True,
        "max_pain_bias": "BULLISH_MAGNET" if bullish else "BEARISH_MAGNET",
        "order_book_imbalance_available": True,
        "order_book_direction": direction,
        "vwap_direction_available": True,
        "vwap_direction": direction,
        "vwap_bounce": "RETEST_ZONE",
        "chain_available": True,
        "chain_imbalance": 1.4 if bullish else 0.65,
        "oi_chain_noise": "PUT_NOISE" if bullish else "CALL_NOISE",
        "gift_nifty_available": True,
        "gift_nifty": direction,
        "ad_ratio_available": True,
        "ad_ratio": 1.3 if bullish else 0.7,
        "breadth_available": True,
        "bullish_stocks_count": 4 if bullish else 1,
        "total_oi_available": True,
        "total_oi_volume": 30000000,
        "price_action_available": True,
        "price_action": "BREAKOUT" if bullish else "BREAKDOWN",
        "volume_shock_available": True,
        "volume_shock": "HIGH",
        "tick_pressure_available": True,
        "tick_pressure_direction": direction,
        "tick_acceleration_available": True,
        "tick_acceleration_direction": direction,
        "micro_trend_available": True,
        "micro_trend_direction": direction,
        "price_efficiency_available": True,
        "price_efficiency_direction": direction,
    }


def main():
    bullish = assert_side(payload("BULLISH"), "BUY", "Bullish institutional consensus selects CE direction")
    bearish = assert_side(payload("BEARISH"), "SELL", "Bearish institutional consensus selects PE direction")
    bull_names = {row["condition"] for row in bullish["buy_condition_points"]}
    bear_names = {row["condition"] for row in bearish["sell_condition_points"]}
    if bullish["buy_available"] != bearish["sell_available"] or bull_names != bear_names:
        raise AssertionError("CE and PE condition sets are not symmetric")
    if any(row["max_points"] != 1 for row in bullish["buy_condition_points"] + bearish["sell_condition_points"]):
        raise AssertionError("A scoring condition has unequal point weight")
    print(f"PASS: CE and PE use the same {len(bull_names)} conditions with equal 1-point weight")
    mixed = payload("BULLISH")
    for key in ("index_sync_direction", "top_weighted_confirmation", "top15_weighted_confirmation", "banknifty_vwap_confirmation", "greeks_bias", "vwap_direction", "tick_pressure_direction"):
        mixed[key] = "BEARISH"
    assert_side(mixed, "HOLD", "Critical directional conflict blocks execution")
    print("\nSCORING TEST RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
