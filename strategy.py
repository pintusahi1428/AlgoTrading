import math
import os
import time

from adaptive_engine import adjust_threshold, get_market_regime


class Strategy:
    def __init__(self):
        self.reset_daily_session()

    def reset_daily_session(self):
        self.pv_total = {"NIFTY": 0.0, "BANKNIFTY": 0.0}
        self.vol_total = {"NIFTY": 0.0, "BANKNIFTY": 0.0}

    def calculate_real_vwap(self, name, price, volume):
        try:
            key = "NIFTY" if name == "NIFTY" else "BANKNIFTY"
            volume = float(volume)
            if volume <= 0:
                return float(price)
            self.pv_total[key] += float(price) * volume
            self.vol_total[key] += volume
            return round(self.pv_total[key] / self.vol_total[key], 2)
        except Exception:
            return float(price)


_strategy = Strategy()
vwap_engine = _strategy
calculate_real_vwap = _strategy.calculate_real_vwap
reset_daily_session = _strategy.reset_daily_session


def _direction_matches(action, direction):
    expected = "BULLISH" if action == "BUY" else "BEARISH"
    return str(direction).upper() == expected


def _score_action(inputs, action):
    checks = []

    def directional(name, available, direction, critical=False):
        if available:
            checks.append({
                "name": name,
                "matched": _direction_matches(action, direction),
                "opposed": _direction_matches("SELL" if action == "BUY" else "BUY", direction),
                "critical": critical,
                "observed": str(direction),
                "max_points": 1,
            })

    def common(name, available, matched):
        if available:
            checks.append({"name": name, "matched": bool(matched), "opposed": False, "critical": False, "observed": "PASS" if matched else "FAIL", "max_points": 1})

    regime = get_market_regime(inputs.get("prices_history", []))
    smc = int(inputs.get("smc_score", 0))
    common("regime_tradeable", True, regime not in ("LOW_ATR_DEAD",))
    common("regime_quality", True, regime in ("STRONG_TREND", "HIGH_VOL_OPEN", "SCALPING"))
    directional("day_type_alignment", inputs.get("day_type_direction_available", False), inputs.get("day_type_direction"), True)
    common("regime_stability", inputs.get("regime_stability_available", False), float(inputs.get("regime_stability", 0.0)) >= 0.18)
    directional("trend_direction", inputs.get("trend_direction_available", False), inputs.get("trend_direction"), True)
    common("iv_percentile", inputs.get("iv_available", False), float(inputs.get("iv_percentile", 50)) <= 45.0)
    common("india_vix", inputs.get("india_vix_available", False), 10.0 <= float(inputs.get("india_vix", 0.0)) <= 24.0)
    directional("index_sync", inputs.get("index_sync_available", True), inputs.get("index_sync_direction"), True)
    directional("top_weighted", inputs.get("top_weighted_available", False), inputs.get("top_weighted_confirmation"), True)
    directional("top15_weighted", inputs.get("top15_weighted_available", False), inputs.get("top15_weighted_confirmation"), True)
    directional("banknifty_vwap", inputs.get("banknifty_vwap_available", False), inputs.get("banknifty_vwap_confirmation"), True)
    directional("banknifty_mtf", inputs.get("banknifty_mtf_available", False), str(inputs.get("banknifty_mtf_confirmation", "")).replace("STRONG_", ""), True)
    momentum = int(inputs.get("momentum", 0))
    directional("momentum", momentum != 0, "BULLISH" if momentum > 0 else "BEARISH", True)
    directional("smc_direction", inputs.get("smc_direction_available", False), inputs.get("smc_direction"), True)
    common("smc_quality_40", True, smc >= 40)
    common("smc_quality_70", True, smc >= 70)
    directional("fvg", inputs.get("fvg_available", False), "BULLISH" if inputs.get("fvg_signal") == "BULLISH_FVG" else "BEARISH", False)
    sweep = inputs.get("strong_sweep")
    sweep_direction = "BULLISH" if sweep == "STRONG_SELL_SIDE_SWEEP" else "BEARISH"
    directional("strong_sweep", inputs.get("strong_sweep_available", False), sweep_direction, False)
    common("smc_volume", inputs.get("smc_volume_confirmation_available", False), inputs.get("smc_volume_confirmation") == "CONFIRMED")
    directional("nifty_mtf", str(inputs.get("mtf_confluence", "")).startswith("STRONG_"), str(inputs.get("mtf_confluence", "")).replace("STRONG_", ""), True)

    oi_map = {"LONG_BUILDUP": "BULLISH", "SHORT_COVERING": "BULLISH", "SHORT_BUILDUP": "BEARISH", "LONG_UNWINDING": "BEARISH"}
    directional("oi_buildup", inputs.get("oi_available", False), oi_map.get(inputs.get("oi_buildup"), "NEUTRAL"), True)
    coi_map = {"CALL_COI_BULLISH": "BULLISH", "PUT_COI_BEARISH": "BEARISH"}
    directional("coi_signal", inputs.get("coi_available", False), coi_map.get(inputs.get("coi_signal"), "NEUTRAL"), True)
    if inputs.get("coi_imbalance_available"):
        ratio = float(inputs.get("coi_imbalance", 1.0))
        directional("coi_imbalance", True, "BULLISH" if ratio >= 1.15 else "BEARISH" if ratio <= 0.85 else "NEUTRAL", True)
    directional("greeks_bias", inputs.get("greeks_available", False), inputs.get("greeks_bias"), True)
    dealer_map = {"DEALER_SUPPORT": "BULLISH", "DEALER_RESISTANCE": "BEARISH"}
    directional("dealer_position", inputs.get("dealer_position_available", False), dealer_map.get(inputs.get("dealer_position_signal"), "NEUTRAL"), True)
    pain_map = {"BULLISH_MAGNET": "BULLISH", "BEARISH_MAGNET": "BEARISH"}
    directional("max_pain", inputs.get("max_pain_available", False), pain_map.get(inputs.get("max_pain_bias"), "NEUTRAL"), False)
    directional("option_order_book", inputs.get("order_book_imbalance_available", False), inputs.get("order_book_direction"), True)
    directional("vwap_direction", inputs.get("vwap_direction_available", True), inputs.get("vwap_direction"), True)
    common("vwap_retest", True, inputs.get("vwap_bounce") == "RETEST_ZONE")

    if inputs.get("chain_available"):
        ratio = float(inputs.get("chain_imbalance", 1.0))
        directional("pcr_imbalance", True, "BULLISH" if ratio >= 1.20 else "BEARISH" if ratio <= 0.80 else "NEUTRAL", True)
        noise = inputs.get("oi_chain_noise")
        directional("option_chain_noise", True, "BULLISH" if noise in ("PUT_NOISE", "STRONG_SUPPORT") else "BEARISH" if noise in ("CALL_NOISE", "STRONG_RESISTANCE") else "NEUTRAL", False)
    directional("gift_nifty", inputs.get("gift_nifty_available", False), inputs.get("gift_nifty"), False)
    if inputs.get("ad_ratio_available"):
        ratio = float(inputs.get("ad_ratio", 1.0))
        directional("advance_decline", True, "BULLISH" if ratio >= 1.15 else "BEARISH" if ratio <= 0.87 else "NEUTRAL", True)
    if inputs.get("breadth_available"):
        count = int(inputs.get("bullish_stocks_count", 0))
        directional("weighted_breadth", True, "BULLISH" if count >= 3 else "BEARISH" if count <= 1 else "NEUTRAL", False)
    common("total_oi_liquidity", inputs.get("total_oi_available", False), float(inputs.get("total_oi_volume", 0.0)) >= (8000000.0 if time.strftime("%H:%M") <= "09:45" else 22000000.0))

    directional("price_action", inputs.get("price_action_available", False), "BULLISH" if inputs.get("price_action") == "BREAKOUT" else "BEARISH", True)
    common("volume_shock", inputs.get("volume_shock_available", False), inputs.get("volume_shock") == "HIGH")

    directional("tick_pressure", inputs.get("tick_pressure_available", False), inputs.get("tick_pressure_direction"), True)
    directional("tick_acceleration", inputs.get("tick_acceleration_available", False), inputs.get("tick_acceleration_direction"), False)
    directional("micro_trend", inputs.get("micro_trend_available", False), inputs.get("micro_trend_direction"), True)
    directional("price_efficiency", inputs.get("price_efficiency_available", False), inputs.get("price_efficiency_direction"), False)

    score = sum(1 for check in checks if check["matched"])
    critical_matches = sum(1 for check in checks if check["critical"] and check["matched"])
    critical_conflicts = sum(1 for check in checks if check["critical"] and check["opposed"])
    return {
        "action": action,
        "score": score,
        "available": len(checks),
        "match_percent": score / max(1, len(checks)),
        "critical_matches": critical_matches,
        "critical_conflicts": critical_conflicts,
        "matched_conditions": [check["name"] for check in checks if check["matched"]],
        "conflicting_conditions": [check["name"] for check in checks if check["opposed"]],
        "condition_points": [
            {
                "condition": check["name"],
                "observed": check["observed"],
                "critical": check["critical"],
                "status": "MATCH" if check["matched"] else "CONFLICT" if check["opposed"] else "NO_POINT",
                "earned_points": 1 if check["matched"] else 0,
                "max_points": check["max_points"],
            }
            for check in checks
        ],
    }


def get_ai_score(inputs, is_reentry=False):
    result = _score_action(inputs, inputs.get("action", "BUY"))
    inputs["available_conditions"] = result["available"]
    inputs["score_details"] = result
    return result["score"], result["critical_matches"] >= 4


def get_signal(inputs, is_reentry=False):
    try:
        buy = _score_action(inputs, "BUY")
        sell = _score_action(inputs, "SELL")
        winner, runner_up = (buy, sell) if buy["score"] >= sell["score"] else (sell, buy)
        lead = winner["score"] - runner_up["score"]
        regime = get_market_regime(inputs.get("prices_history", []))
        min_available = int(os.getenv("MIN_AVAILABLE_CONDITIONS", "24"))
        min_score = int(os.getenv("MIN_SIGNAL_SCORE", "18"))
        base_percent = float(os.getenv("MIN_MATCH_PERCENT", "0.67"))
        required_percent = base_percent
        if regime in ("SIDEWAYS_CHOPPY", "LOW_ATR_DEAD"):
            required_percent = max(required_percent, float(os.getenv("CHOPPY_MATCH_PERCENT", "0.74")))
        elif regime in ("STRONG_TREND", "HIGH_VOL_OPEN"):
            required_percent = max(0.62, required_percent - 0.03)
        required = max(min_score, int(math.ceil(winner["available"] * required_percent)), adjust_threshold(regime, time.strftime("%H:%M")))
        required += max(0, int(inputs.get("day_threshold_adjustment", 0)))
        min_lead = int(os.getenv("MIN_DIRECTION_LEAD", "4"))
        min_critical = int(os.getenv("MIN_CRITICAL_MATCHES", "5"))
        max_conflicts = int(os.getenv("MAX_CRITICAL_CONFLICTS", "2"))
        passed = (
            winner["available"] >= min_available
            and winner["score"] >= required
            and lead >= min_lead
            and winner["critical_matches"] >= min_critical
            and winner["critical_conflicts"] <= max_conflicts
        )
        inputs["available_conditions"] = winner["available"]
        inputs["score_details"] = winner
        return {
            "side": winner["action"] if passed else "HOLD",
            "candidate_side": winner["action"],
            "confidence": winner["score"],
            "required": required,
            "available": winner["available"],
            "match_percent": round(winner["match_percent"] * 100.0, 1),
            "direction_lead": lead,
            "critical_matches": winner["critical_matches"],
            "critical_conflicts": winner["critical_conflicts"],
            "matched_conditions": winner["matched_conditions"],
            "conflicting_conditions": winner["conflicting_conditions"],
            "condition_points": winner["condition_points"],
            "buy_score": buy["score"],
            "sell_score": sell["score"],
            "buy_available": buy["available"],
            "sell_available": sell["available"],
            "buy_condition_points": buy["condition_points"],
            "sell_condition_points": sell["condition_points"],
            "is_strong": winner["critical_matches"] >= min_critical + 2,
        }
    except Exception:
        return {"side": "HOLD", "candidate_side": "HOLD", "confidence": 0, "required": 0, "available": 0, "match_percent": 0.0, "direction_lead": 0, "critical_matches": 0, "critical_conflicts": 0, "condition_points": [], "buy_score": 0, "sell_score": 0, "buy_condition_points": [], "sell_condition_points": [], "is_strong": False}
