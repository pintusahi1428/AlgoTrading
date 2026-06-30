import os


def trailing_stop_logic(trade_payload, curr_ltp, current_sl, qty):
    try:
        curr_ltp = float(curr_ltp)
        current_sl = float(current_sl)
        entry = float(trade_payload.get("avg_entry_prem", curr_ltp))
        regime = str(trade_payload.get("regime", "NORMAL")).upper()
        atr_points = max(1.0, float(trade_payload.get("atr_points", 12.0) or 12.0))
        pnl_pts = curr_ltp - entry
        trade_payload["max_prem"] = max(float(trade_payload.get("max_prem", curr_ltp)), curr_ltp)
        peak = trade_payload["max_prem"]

        if regime in ("STRONG_TREND", "HIGH_VOL_OPEN"):
            pre_lock_buffer = max(12.0, min(24.0, atr_points * 1.35))
        elif regime in ("SIDEWAYS_CHOPPY", "LOW_ATR_DEAD", "CHOPPY"):
            pre_lock_buffer = max(8.0, min(16.0, atr_points * 1.05))
        else:
            pre_lock_buffer = max(10.0, min(20.0, atr_points * 1.20))

        strong_lock_trigger = float(os.getenv("TRAIL_STRONG_LOCK_TRIGGER", "30"))
        strong_lock_points = float(os.getenv("TRAIL_STRONG_LOCK_POINTS", "23"))
        continuous_gap = float(os.getenv("TRAIL_CONTINUOUS_GAP_POINTS", "12"))
        lock_levels = ((10.0, 0.0), (20.0, 5.0), (strong_lock_trigger, strong_lock_points))

        for trigger, lock_pts in lock_levels:
            if pnl_pts >= trigger:
                current_sl = max(current_sl, entry + lock_pts)
                trade_payload["shield_activated"] = True
                trade_payload["profit_lock_level"] = trigger
        if pnl_pts >= strong_lock_trigger:
            current_sl = max(current_sl, entry + strong_lock_points, peak - continuous_gap)
            trail_buffer = continuous_gap
            trade_payload["continuous_trailing_active"] = True
        else:
            trail_buffer = pre_lock_buffer
            if pnl_pts >= pre_lock_buffer:
                current_sl = max(current_sl, peak - pre_lock_buffer)

        highest = max(float(trade_payload.get("highest_sl_recorded", current_sl)), current_sl)
        trade_payload["highest_sl_recorded"] = highest
        trade_payload["lock_pts"] = max(0.0, highest - entry)
        trade_payload["trail_buffer"] = trail_buffer
        return highest
    except Exception:
        return current_sl


def smart_exit_signal(curr_ltp, sl_threshold):
    try:
        return float(curr_ltp) <= float(sl_threshold)
    except Exception:
        return False
