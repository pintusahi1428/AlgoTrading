import math
import os
from datetime import datetime


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _std(values):
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / len(values))


class RegimeDetectionEngine:
    """Scale-free intraday day-type classifier with expiry and gap overlays."""

    def __init__(self):
        self.session_date = None
        self.open_price = None

    def reset(self):
        self.session_date = None
        self.open_price = None

    def analyze(self, prices, volumes=None, now=None, previous_close=None, expiry_date=None):
        now = now or datetime.now()
        session_date = now.date()
        values = [float(value) for value in prices[-90:] if value is not None]
        if self.session_date != session_date:
            self.session_date = session_date
            self.open_price = values[0] if values else None
        if values and self.open_price is None:
            self.open_price = values[0]

        result = {
            "day_type": "WARMUP",
            "day_type_direction": "NEUTRAL",
            "day_type_direction_available": False,
            "regime_stability": 0.0,
            "regime_stability_available": False,
            "is_trend_day": False,
            "is_range_day": False,
            "is_expiry_day": bool(expiry_date and expiry_date == session_date),
            "is_gap_day": False,
            "gap_percent": 0.0,
            "trend_efficiency": 0.0,
            "day_risk_multiplier": 0.60,
            "day_threshold_adjustment": 2,
        }
        if len(values) < 20:
            return result

        changes = [values[i] - values[i - 1] for i in range(1, len(values))]
        recent = changes[-30:]
        sigma = max(_std(recent), 0.01)
        path = sum(abs(value) for value in recent)
        net = values[-1] - values[-min(31, len(values))]
        efficiency = abs(net) / max(path, 0.01)
        directional_ratio = abs(sum(1 if value > 0 else -1 if value < 0 else 0 for value in recent)) / max(1, len(recent))
        range_z = (max(values[-30:]) - min(values[-30:])) / sigma
        trend_strength = min(1.0, (efficiency * 0.65) + (directional_ratio * 0.20) + (min(range_z / 8.0, 1.0) * 0.15))
        range_strength = min(1.0, ((1.0 - efficiency) * 0.70) + ((1.0 - directional_ratio) * 0.30))

        previous_close = float(previous_close or 0.0)
        opening = float(self.open_price or values[0])
        gap_percent = ((opening - previous_close) / previous_close * 100.0) if previous_close > 0 else 0.0
        gap_limit = float(os.getenv("GAP_DAY_THRESHOLD_PERCENT", "0.35"))
        is_gap = abs(gap_percent) >= gap_limit
        is_trend = trend_strength >= float(os.getenv("TREND_DAY_MIN_STRENGTH", "0.62")) and efficiency >= 0.42
        is_range = not is_trend and range_strength >= 0.58
        is_expiry = bool(expiry_date and expiry_date == session_date)
        direction = "BULLISH" if net > sigma else "BEARISH" if net < -sigma else "NEUTRAL"

        if is_expiry:
            day_type = "EXPIRY_DAY"
            risk_multiplier, threshold_adjustment = 0.65, 2
        elif is_gap:
            day_type = "GAP_DAY"
            risk_multiplier, threshold_adjustment = 0.70, 1
            direction = "BULLISH" if gap_percent > 0 else "BEARISH"
        elif is_trend:
            day_type = "TREND_DAY"
            risk_multiplier, threshold_adjustment = 1.0, 0
        else:
            day_type = "RANGE_DAY"
            risk_multiplier, threshold_adjustment = 0.65, 2

        stability = abs(trend_strength - range_strength)
        return {
            "day_type": day_type,
            "day_type_direction": direction,
            "day_type_direction_available": direction != "NEUTRAL" and day_type in ("TREND_DAY", "GAP_DAY"),
            "regime_stability": round(stability, 4),
            "regime_stability_available": True,
            "is_trend_day": is_trend,
            "is_range_day": is_range,
            "is_expiry_day": is_expiry,
            "is_gap_day": is_gap,
            "gap_percent": round(gap_percent, 4),
            "trend_efficiency": round(efficiency, 4),
            "trend_strength": round(trend_strength, 4),
            "range_strength": round(range_strength, 4),
            "day_risk_multiplier": risk_multiplier,
            "day_threshold_adjustment": threshold_adjustment,
        }
