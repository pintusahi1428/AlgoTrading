import ctypes
import json
import os
import sys
import time
import warnings
from datetime import datetime

import pyotp
import requests
from dotenv import load_dotenv
from SmartApi import SmartConnect

from adaptive_engine import (
    adjust_threshold,
    analyze_oi_buildup,
    calculate_mtf_trend,
    get_market_regime,
)
from data_fetcher import DataFetcher
from logger import logger
from regime_engine import RegimeDetectionEngine
from smart_money import detect_fvg, directional_smc_score, smart_money_analysis, smc_score
from strategy import calculate_real_vwap, get_signal, reset_daily_session
from token_manager import get_atm_option, get_nearest_expiry_date, get_option_lot_size, load_tokens_once
from trade_manager import TradeManager
from walk_forward_optimizer import apply_optimized_params, optimize_weekly


warnings.filterwarnings("ignore")
load_dotenv()

TOKENS = {"NIFTY": "26000", "BANKNIFTY": "26009"}
HISTORY_LIMIT = 90
market_data = {"h_nifty": [], "h_bn": [], "prev_nifty": 0.0, "prev_bn": 0.0}
broker_state = {"obj": None}
day_regime_engine = RegimeDetectionEngine()


def setup_console():
    try:
        os.system("chcp 65001 >NUL")
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def env_required(name):
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"Missing required env value: {name}")
    return str(value).strip()


def establish_broker_session():
    api_key = env_required("API_KEY")
    client_id = env_required("CLIENT_ID")
    pin = env_required("PIN")
    totp_secret = env_required("BROKER_TOTP_SECRET")
    obj = SmartConnect(api_key=api_key)
    totp = pyotp.TOTP(totp_secret).now()
    session = obj.generateSession(client_id, pin, totp)
    if session and session.get("status"):
        return obj
    raise RuntimeError(f"Angel One session failed: {session}")


def refresh_broker_session():
    broker_state["obj"] = establish_broker_session()
    return broker_state["obj"]


def is_market_open():
    now = datetime.now()
    if now.weekday() in (5, 6):
        return False
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= now <= end


def clear_screen():
    sys.stdout.write("\033[2J\033[H")


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def calculate_atr_proxy(prices, period=14):
    try:
        if len(prices) < period + 1:
            return 12.0
        diffs = [abs(float(prices[i]) - float(prices[i - 1])) for i in range(len(prices) - period, len(prices))]
        return max(4.0, round(sum(diffs) / len(diffs), 2))
    except Exception:
        return 12.0


def calculate_indicator_pack(prices, volumes):
    pack = {
        "price_action_available": False,
        "volume_shock_available": False,
    }
    if len(prices) >= 12:
        recent_high = max(prices[-12:-1])
        recent_low = min(prices[-12:-1])
        if prices[-1] > recent_high:
            pack.update({"price_action": "BREAKOUT", "price_action_available": True})
        elif prices[-1] < recent_low:
            pack.update({"price_action": "BREAKDOWN", "price_action_available": True})
    if len(volumes) >= 20:
        avg_vol = sum(volumes[-20:-1]) / 19.0
        if avg_vol > 0 and volumes[-1] >= avg_vol * 1.8:
            pack.update({"volume_shock": "HIGH", "volume_shock_available": True})
    return pack


def volume_confirmation(volumes):
    try:
        if len(volumes) < 20:
            return None
        avg_vol = sum(volumes[-20:-1]) / 19.0
        if avg_vol > 0 and volumes[-1] >= avg_vol * 1.25:
            return "CONFIRMED"
    except Exception:
        pass
    return None


def calculate_microstructure_pack(prices):
    """Use real one-second LTP snapshots; this is retail tick pressure, not exchange L2."""
    pack = {
        "tick_pressure_available": False,
        "tick_acceleration_available": False,
        "micro_trend_available": False,
        "price_efficiency_available": False,
    }
    try:
        window = [float(value) for value in prices[-24:]]
        if len(window) < 12:
            return pack
        diffs = [window[i] - window[i - 1] for i in range(1, len(window))]
        absolute_path = sum(abs(value) for value in diffs)
        if absolute_path <= 0:
            return pack
        signed_pressure = sum(diffs) / absolute_path
        pack.update({
            "tick_pressure_available": True,
            "tick_pressure": round(signed_pressure, 4),
            "tick_pressure_direction": "BULLISH" if signed_pressure >= 0.18 else "BEARISH" if signed_pressure <= -0.18 else "NEUTRAL",
        })
        recent_velocity = sum(diffs[-3:]) / 3.0
        prior_velocity = sum(diffs[-9:-3]) / 6.0
        acceleration = recent_velocity - prior_velocity
        scale = max(sum(abs(value) for value in diffs) / len(diffs), 0.01)
        pack.update({
            "tick_acceleration_available": True,
            "tick_acceleration": round(acceleration / scale, 4),
            "tick_acceleration_direction": "BULLISH" if acceleration >= scale * 0.25 else "BEARISH" if acceleration <= -scale * 0.25 else "NEUTRAL",
        })
        micro_move = window[-1] - window[-8]
        pack.update({
            "micro_trend_available": True,
            "micro_trend_direction": "BULLISH" if micro_move >= scale else "BEARISH" if micro_move <= -scale else "NEUTRAL",
        })
        efficiency = abs(window[-1] - window[0]) / absolute_path
        pack.update({
            "price_efficiency_available": True,
            "price_efficiency": round(efficiency, 4),
            "price_efficiency_direction": "BULLISH" if efficiency >= 0.28 and window[-1] > window[0] else "BEARISH" if efficiency >= 0.28 and window[-1] < window[0] else "NEUTRAL",
        })
    except Exception:
        pass
    return pack


def trade_grade(score, required, available):
    if available <= 0:
        return "B"
    edge = score - required
    match_pct = score / available
    if edge >= 4 and match_pct >= 0.70:
        return "A+"
    if edge >= 2 and match_pct >= 0.58:
        return "A"
    return "B"


def write_signal_score_report(signal, strategy_inputs):
    try:
        os.makedirs("logs", exist_ok=True)
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "signal": signal.get("side", "HOLD"),
            "candidate": signal.get("candidate_side", "HOLD"),
            "required": signal.get("required", 0),
            "buy_score": signal.get("buy_score", 0),
            "sell_score": signal.get("sell_score", 0),
            "buy_available": signal.get("buy_available", 0),
            "sell_available": signal.get("sell_available", 0),
            "buy_conditions": signal.get("buy_condition_points", []),
            "sell_conditions": signal.get("sell_condition_points", []),
            "missing_live_factors": strategy_inputs.get("missing_factors", []),
        }
        target = os.path.join("logs", "last_signal_points.json")
        temp = target + ".tmp"
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(temp, target)
    except Exception as exc:
        logger.error(f"Signal point report write failed: {exc}")


def render_dashboard(status, p, bn, real_vwap, bn_vwap, regime, sig, target, score_smc, tm, data_ok, block_reason):
    payload = tm.get_dashboard_payload()
    vwap_dist = abs(p - real_vwap) if p and real_vwap else 0.0
    nifty_mode = "BULLISH" if p > real_vwap else "BEARISH"
    bn_mode = "BULLISH" if bn > bn_vwap else "BEARISH"
    clear_screen()
    print("=" * 112)
    print(f"MASTER SNIPER v12.0 LIVE | STATUS: {status:<32} | DATA: {'OK' if data_ok else 'BLOCKED'}")
    print("=" * 112)
    print(f"Capital          : INR {payload['current_capital']:,.2f}")
    print(f"NIFTY Spot       : {p:,.2f} | VWAP {real_vwap:,.2f} | Bias {nifty_mode} | Dist {vwap_dist:.2f}")
    print(f"BANKNIFTY Spot   : {bn:,.2f} | VWAP {bn_vwap:,.2f} | Bias {bn_mode}")
    print(f"Market Regime    : {regime:<18} | SMC {score_smc:>3}% | Signal {sig.get('side', 'HOLD'):<4} | Score {sig.get('confidence', 0)}/{sig.get('required', target)} | Available {sig.get('available', 0)}")
    print(f"Day Regime       : {sig.get('day_type', 'NA'):<12} | Direction {sig.get('day_type_direction', 'NA'):<7} | Stability {sig.get('regime_stability', 0.0):>5.2f} | Gap {sig.get('gap_percent', 0.0):>6.2f}%")
    print(f"Consensus        : Candidate {sig.get('candidate_side', 'HOLD'):<4} | Match {sig.get('match_percent', 0.0):>5.1f}% | Lead {sig.get('direction_lead', 0):>2} | Critical {sig.get('critical_matches', 0)} OK / {sig.get('critical_conflicts', 0)} conflict")
    print(f"Direction Points : CE/BUY {sig.get('buy_score', 0):>2}/{sig.get('buy_available', 0):<2} | PE/BUY {sig.get('sell_score', 0):>2}/{sig.get('sell_available', 0):<2} | Every available condition = 1 point")
    print(
        "Institutional    : "
        f"Greeks {sig.get('greeks_bias', 'NA'):<8} | "
        f"Dealer {sig.get('dealer_position_signal', 'NA'):<17} | "
        f"MaxPain {sig.get('max_pain', 0):>8.0f} {sig.get('max_pain_bias', 'NA'):<14} | "
        f"Top15 {sig.get('top15_weighted_confirmation', 'NA'):<7} {sig.get('top15_weighted_score', 0.0):>6.2f}"
    )
    print(f"Microstructure   : Best5 {sig.get('order_book_direction', 'NA'):<7} {sig.get('order_book_imbalance', 0.0):>5.2f} | Tick {sig.get('tick_pressure_direction', 'NA'):<7} | Micro trend {sig.get('micro_trend_direction', 'NA'):<7}")
    print(f"Risk             : Booked INR {tm.today_pnl:,.2f} | Net INR {payload['total_pnl']:,.2f} | Peak INR {tm.daily_peak_pnl:,.2f} | Locked floor INR {tm.daily_profit_floor:,.2f}")
    print(f"Day Shield       : {'HALTED - ' + tm.daily_halt_reason if tm.daily_halt else 'ACTIVE'} | Orders {tm.total_trades_today}/{tm.max_trades_per_day}")
    greeks = payload.get("portfolio_greeks", {})
    print(f"Portfolio Greeks : Delta {greeks.get('delta', 0.0):>9.2f} | Gamma {greeks.get('gamma', 0.0):>9.4f} | Theta {greeks.get('theta', 0.0):>9.2f} | Vega {greeks.get('vega', 0.0):>9.2f}")
    execution = payload.get("execution_quality", {})
    print(f"Execution Quality: Rating {execution.get('rating', 'NO_DATA'):<18} | Fills {execution.get('fills', 0):>3} | Missed {execution.get('missed', 0):>3} | Slip {execution.get('avg_slippage', 0.0):>6.2f} | Latency {execution.get('avg_latency_ms', 0.0):>7.0f} ms")
    if block_reason:
        print(f"Live Entry Block : {block_reason}")
    print("-" * 112)
    print(f"{'#':<3} {'SYMBOL':<25} {'TYPE':<4} {'G':<2} {'QTY':>5} {'ENTRY':>8} {'CURR':>8} {'INIT SL':>8} {'TRAIL':>8} {'B-SL':>8} {'LOCK':>7} {'TGT':>7} {'PNL':>10}")
    print("-" * 112)
    rows = payload["executed_trades_list"]
    if not rows:
        print(f"{'-':<3} {'NO ACTIVE TRADE':<25} {'-':<4} {'-':<2} {0:>5} {0:>8.2f} {0:>8.2f} {0:>8.2f} {0:>8.2f} {0:>8.2f} {0:>7.2f} {0:>7.2f} {0:>10.2f}")
    else:
        for idx, row in enumerate(rows, 1):
            print(
                f"{idx:<3} {row['symbol']:<25} {row['type']:<4} {row.get('grade', 'B'):<2} {int(row['qty']):>5} "
                f"{row['entry']:>8.2f} {row['ltp']:>8.2f} {row['initial_sl']:>8.2f} "
                f"{row['trail_sl']:>8.2f} {row['broker_sl']:>8.2f} {row['locked']:>7.2f} "
                f"{row.get('target_points', 0.0):>7.2f} {row['pnl']:>10.2f}"
            )
            if row.get("sl_order_id"):
                print(f"    Broker SL Order ID: {row['sl_order_id']}")
    print("-" * 112)
    print(f"CE booked PnL: INR {tm.total_ce_pnl:,.2f} | PE booked PnL: INR {tm.total_pe_pnl:,.2f} | Hard square-off: 15:15")
    print("-" * 112)
    print("BROKER OPEN POSITIONS")
    print(f"{'SYMBOL':<32} {'QTY':>6} {'BUY AVG':>10} {'SELL AVG':>10} {'LTP':>10} {'BROKER PNL':>12} {'PRODUCT':>10}")
    broker_rows = payload.get("broker_positions_list", [])
    if not broker_rows:
        print(f"{'NO OPEN BROKER POSITION':<32} {0:>6} {0:>10.2f} {0:>10.2f} {0:>10.2f} {0:>12.2f} {'-':>10}")
    else:
        for row in broker_rows:
            print(
                f"{row['symbol']:<32} {row['qty']:>6} {row['buy_avg']:>10.2f} "
                f"{row['sell_avg']:>10.2f} {row['ltp']:>10.2f} {row['pnl']:>12.2f} {row['product']:>10}"
            )
    print("=" * 112)


def build_strategy_inputs(fetcher, p, bn, h_n, h_bn, real_vwap, bn_vwap, volumes, previous_close=None, expiry_date=None):
    score, smc_direction, smc_data = directional_smc_score(h_n)
    regime = get_market_regime(h_n)
    atr_points = calculate_atr_proxy(h_n)
    fvg_signal = detect_fvg(h_n)
    momentum = 1 if len(h_n) > 1 and p > h_n[-2] else -1
    bn_up = bn > (market_data["prev_bn"] if market_data["prev_bn"] > 0 else bn)
    sync_yes = (p > real_vwap and bn_up) or (p < real_vwap and not bn_up)
    index_sync_direction = "BULLISH" if p > real_vwap and bn_up else "BEARISH" if p < real_vwap and not bn_up else "NEUTRAL"
    live_factors = fetcher.get_live_market_factors("NIFTY", p)
    price_change = p - (market_data["prev_nifty"] if market_data["prev_nifty"] > 0 else p)
    oi_buildup = analyze_oi_buildup(0, price_change)
    mtf = calculate_mtf_trend(h1_prices=h_n[-5:], h5_prices=h_n[-20:], m15_prices=h_n)
    bn_mtf = calculate_mtf_trend(h1_prices=h_bn[-5:], h5_prices=h_bn[-20:], m15_prices=h_bn)
    bn_vwap_confirmation = "BULLISH" if bn > bn_vwap else "BEARISH"
    smc_vol_confirm = volume_confirmation(volumes)
    technicals = calculate_indicator_pack(h_n, volumes)
    day_regime = day_regime_engine.analyze(h_n, volumes, previous_close=previous_close, expiry_date=expiry_date)
    payload = {
        "action": "BUY" if p > real_vwap else "SELL",
        "smc_score": score,
        "trend": regime,
        "trend_direction": "BULLISH" if h_n[-1] > h_n[-min(8, len(h_n))] else "BEARISH",
        "trend_direction_available": len(h_n) >= 8,
        "momentum": momentum,
        "index_sync": sync_yes,
        "index_sync_available": True,
        "index_sync_direction": index_sync_direction,
        "atr_points": atr_points,
        "vwap_dist": abs(p - real_vwap),
        "vwap_bounce": "RETEST_ZONE" if abs(p - real_vwap) <= 12.0 else "AWAY_FROM_VWAP",
        "vwap_direction": "BULLISH" if p > real_vwap else "BEARISH",
        "vwap_direction_available": True,
        "prices_history": h_n,
        "mtf_confluence": mtf,
        "oi_buildup": oi_buildup,
        "oi_available": False,
        "smc_direction": smc_direction,
        "smc_direction_available": smc_direction != "NEUTRAL",
        "fvg_signal": fvg_signal or "NONE",
        "fvg_available": fvg_signal is not None,
        "strong_sweep": smc_data.get("strong_sweep") or "NONE",
        "strong_sweep_available": smc_data.get("strong_sweep") is not None,
        "smc_volume_confirmation": smc_vol_confirm or "NONE",
        "smc_volume_confirmation_available": smc_vol_confirm is not None,
        "banknifty_vwap_confirmation": bn_vwap_confirmation,
        "banknifty_vwap_available": True,
        "banknifty_mtf_confirmation": bn_mtf,
        "banknifty_mtf_available": bn_mtf.startswith("STRONG_"),
        "missing_factors": live_factors["missing"],
    }
    payload.update(live_factors)
    payload.update(technicals)
    payload.update(calculate_microstructure_pack(h_n))
    payload.update(day_regime)
    return payload, regime, score


def maybe_open_trade(tm, sig, p, regime, score_smc, strategy_inputs):
    allowed, reason = tm.can_open_new_trade()
    if not allowed:
        if sig.get("side") in ("BUY", "SELL"):
            tm.record_missed_trade(reason, sig, strategy_inputs)
        return reason
    if sig.get("side") not in ("BUY", "SELL"):
        return "Signal is HOLD"
    max_active = int(os.getenv("MAX_ACTIVE_TRADES", "2"))
    if len(tm.active_trades) >= max_active:
        reason = f"Active trade cap reached ({len(tm.active_trades)}/{max_active})"
        tm.record_missed_trade(reason, sig, strategy_inputs)
        return reason
    option_type = "CE" if sig["side"] == "BUY" else "PE"
    greek_prefix = "call" if option_type == "CE" else "put"
    allow_opposite = str(os.getenv("ALLOW_OPPOSITE_ACTIVE", "FALSE")).strip().upper() == "TRUE"
    if not allow_opposite and any(t.get("type") != option_type for t in tm.active_trades):
        reason = "Opposite-side active trade running"
        tm.record_missed_trade(reason, sig, strategy_inputs)
        return reason
    cooldown = int(os.getenv("REENTRY_COOLDOWN_SECONDS", "45"))
    is_reentry, reentry_reason = tm.evaluate_same_direction_reentry(option_type, regime, sig, strategy_inputs)
    if time.time() - tm.last_exit_time < cooldown and not is_reentry:
        reason = "Re-entry cooldown active"
        tm.record_missed_trade(reason, sig, strategy_inputs)
        return reason
    symbol, token = get_atm_option("NIFTY", p, option_type)
    if not symbol or not token:
        reason = f"ATM {option_type} token unavailable"
        tm.record_missed_trade(reason, sig, strategy_inputs)
        return reason
    fallback_lot = int(os.getenv("LOT_SIZE_NIFTY", "65"))
    lot_size = get_option_lot_size(symbol, token, fallback=fallback_lot)
    opened = tm.open_trade({
        "index_name": "NIFTY",
        "symbol": symbol,
        "token": token,
        "lot_size": lot_size,
        "side": "BUY",
        "type": option_type,
        "regime": regime,
        "smc_score": score_smc,
        "atr_points": strategy_inputs.get("atr_points", 12.0),
        "target_points": float(strategy_inputs.get("atr_points", 12.0)) * float(os.getenv("ATR_TARGET_MULTIPLIER", "2.2")),
        "trade_grade": trade_grade(int(sig.get("confidence", 0)), int(sig.get("required", 0)), int(sig.get("available", 0))),
        "signal_score": int(sig.get("confidence", 0)),
        "signal_available": int(sig.get("available", 0)),
        "match_percent": float(sig.get("match_percent", 0.0)),
        "direction_lead": int(sig.get("direction_lead", 0)),
        "critical_matches": int(sig.get("critical_matches", 0)),
        "matched_conditions": list(sig.get("matched_conditions", [])),
        "condition_points": list(sig.get("condition_points", [])),
        "day_type": strategy_inputs.get("day_type", "UNKNOWN"),
        "day_risk_multiplier": float(strategy_inputs.get("day_risk_multiplier", 1.0)),
        "regime_stability": float(strategy_inputs.get("regime_stability", 0.0)),
        "decision_epoch": time.time(),
        "is_reentry": is_reentry,
        "reentry_reason": reentry_reason if is_reentry else "",
        "option_delta": float(strategy_inputs.get(f"{greek_prefix}_delta", 0.0) or 0.0),
        "option_gamma": float(strategy_inputs.get(f"{greek_prefix}_gamma", 0.0) or 0.0),
        "option_theta": float(strategy_inputs.get(f"{greek_prefix}_theta", 0.0) or 0.0),
        "option_vega": float(strategy_inputs.get(f"{greek_prefix}_vega", 0.0) or 0.0),
    })
    if opened and is_reentry:
        logger.info(f"Strong-trend same-direction re-entry opened: {symbol} | {reentry_reason}")
    if not opened:
        tm.record_missed_trade("Trade manager risk gate rejected the entry", sig, strategy_inputs)
    return "" if opened else "Trade manager risk gate rejected the entry"


def main():
    setup_console()
    applied = apply_optimized_params()
    if str(os.getenv("WFO_AUTO_UPDATE", "TRUE")).strip().upper() == "TRUE":
        optimization = optimize_weekly(force=False)
        if optimization.get("status") == "UPDATED":
            applied = apply_optimized_params()
    if applied:
        logger.info(f"Applied walk-forward parameters: {applied}")
    obj = refresh_broker_session()
    tm = TradeManager(obj, session_refresher=refresh_broker_session)
    if not load_tokens_once():
        raise RuntimeError("Token master unavailable")
    nearest_expiry = get_nearest_expiry_date("NIFTY")
    fetcher = DataFetcher(obj, session_refresher=refresh_broker_session)
    vwap_session_date = time.strftime("%Y-%m-%d")
    last_wfo_check = time.time()
    print("Connection verified. Starting live dashboard...")
    time.sleep(1)

    while True:
        data_ok = False
        block_reason = ""
        p = market_data["prev_nifty"]
        bn = market_data["prev_bn"]
        sig = {"side": "HOLD", "confidence": 0}
        regime = "WAITING"
        score_smc = 0
        target = 0
        real_vwap = p or 0
        bn_vwap = bn or 0
        try:
            if time.time() - last_wfo_check >= float(os.getenv("WFO_CHECK_INTERVAL_SECONDS", "3600")):
                optimization = optimize_weekly(force=False)
                if optimization.get("status") == "UPDATED":
                    applied = apply_optimized_params()
                    logger.info(f"Applied scheduled walk-forward parameters: {applied}")
                last_wfo_check = time.time()
            today = time.strftime("%Y-%m-%d")
            if today != vwap_session_date:
                reset_daily_session()
                market_data["h_nifty"].clear()
                market_data["h_bn"].clear()
                market_data.setdefault("vol_nifty", []).clear()
                market_data.setdefault("vol_bn", []).clear()
                market_data["prev_nifty"] = 0.0
                market_data["prev_bn"] = 0.0
                day_regime_engine.reset()
                nearest_expiry = get_nearest_expiry_date("NIFTY")
                vwap_session_date = today
            tm.refresh_day_if_needed()
            live_open = is_market_open()
            status = "ACTIVE LIVE MARKET" if live_open else "MARKET CLOSED - ENTRY OFF"

            if live_open and tm.active_trades:
                try:
                    tm.manage_trade(current_spot=p)
                except Exception as manage_exc:
                    logger.exception(f"Independent active-trade management failed: {manage_exc}")
            if live_open:
                tm.enforce_daily_equity_shield()

            n_r = fetcher.fetch_ltp_with_retry("NSE", "NIFTY", TOKENS["NIFTY"])
            if broker_state["obj"] is not tm.broker:
                tm.set_broker(broker_state["obj"])
            if broker_state["obj"] is not fetcher.broker:
                fetcher.set_broker(broker_state["obj"])
            bn_r = fetcher.fetch_ltp_with_retry("NSE", "BANKNIFTY", TOKENS["BANKNIFTY"])
            if broker_state["obj"] is not tm.broker:
                tm.set_broker(broker_state["obj"])
            if broker_state["obj"] is not fetcher.broker:
                fetcher.set_broker(broker_state["obj"])
            if not n_r.get("success") or not bn_r.get("success"):
                block_reason = "Live index feed unavailable"
            else:
                data_ok = True
                p = safe_float(n_r["data"]["ltp"])
                bn = safe_float(bn_r["data"]["ltp"])
                vol_nifty = safe_float(n_r["data"].get("volume"), 1.0) or 1.0
                vol_bn = safe_float(bn_r["data"].get("volume"), 1.0) or 1.0
                previous_close = safe_float((n_r.get("raw") or {}).get("data", {}).get("close"), 0.0)
                market_data.setdefault("vol_nifty", []).append(vol_nifty)
                market_data["vol_nifty"] = market_data["vol_nifty"][-HISTORY_LIMIT:]
                market_data.setdefault("vol_bn", []).append(vol_bn)
                market_data["vol_bn"] = market_data["vol_bn"][-HISTORY_LIMIT:]
                market_data["h_nifty"].append(p)
                market_data["h_bn"].append(bn)
                market_data["h_nifty"] = market_data["h_nifty"][-HISTORY_LIMIT:]
                market_data["h_bn"] = market_data["h_bn"][-HISTORY_LIMIT:]
                real_vwap = calculate_real_vwap("NIFTY", p, vol_nifty)
                bn_vwap = calculate_real_vwap("BANKNIFTY", bn, vol_bn)

                if not live_open:
                    block_reason = "Market closed - institutional factor polling paused"
                elif len(market_data["h_nifty"]) < 20:
                    block_reason = "Waiting for live warm-up candles"
                else:
                    strategy_inputs, regime, score_smc = build_strategy_inputs(
                        fetcher, p, bn, market_data["h_nifty"], market_data["h_bn"], real_vwap, bn_vwap, market_data.get("vol_nifty", []), previous_close, nearest_expiry
                    )
                    target = adjust_threshold(regime, time.strftime("%H:%M"))
                    sig = get_signal(strategy_inputs)
                    write_signal_score_report(sig, strategy_inputs)
                    sig.update({
                        "greeks_bias": strategy_inputs.get("greeks_bias", "NEUTRAL"),
                        "dealer_position_signal": strategy_inputs.get("dealer_position_signal", "DEALER_NEUTRAL"),
                        "max_pain": strategy_inputs.get("max_pain", 0.0),
                        "max_pain_bias": strategy_inputs.get("max_pain_bias", "PINNED"),
                        "top15_weighted_confirmation": strategy_inputs.get("top15_weighted_confirmation", "NEUTRAL"),
                        "top15_weighted_score": strategy_inputs.get("top15_weighted_score", 0.0),
                        "order_book_direction": strategy_inputs.get("order_book_direction", "NEUTRAL"),
                        "order_book_imbalance": strategy_inputs.get("order_book_imbalance", 0.0),
                        "tick_pressure_direction": strategy_inputs.get("tick_pressure_direction", "NEUTRAL"),
                        "micro_trend_direction": strategy_inputs.get("micro_trend_direction", "NEUTRAL"),
                        "day_type": strategy_inputs.get("day_type", "UNKNOWN"),
                        "day_type_direction": strategy_inputs.get("day_type_direction", "NEUTRAL"),
                        "regime_stability": strategy_inputs.get("regime_stability", 0.0),
                        "gap_percent": strategy_inputs.get("gap_percent", 0.0),
                    })
                    missing = strategy_inputs.get("missing_factors", [])
                    factor_note = "" if not missing else " | Missing optional live factors: " + "; ".join(missing[:2])
                    if live_open:
                        block_reason = maybe_open_trade(tm, sig, p, regime, score_smc, strategy_inputs)
                        block_reason = (block_reason or "") + factor_note
                    else:
                        block_reason = "Market is closed" + factor_note

            render_dashboard(status, p, bn, real_vwap, bn_vwap, regime, sig, target, score_smc, tm, data_ok, block_reason)
            market_data["prev_nifty"] = p
            market_data["prev_bn"] = bn
            time.sleep(1)
        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except Exception as exc:
            logger.exception(f"Main loop error: {exc}")
            block_reason = str(exc)
            render_dashboard("ERROR - ENTRY BLOCKED", p, bn, real_vwap, bn_vwap, regime, sig, target, score_smc, tm, False, block_reason)
            time.sleep(2)


if __name__ == "__main__":
    main()
