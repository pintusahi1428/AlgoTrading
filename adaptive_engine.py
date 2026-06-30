import math


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _std(values):
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def get_market_regime(history):
    """Classify the current state with scale-free volatility and efficiency."""
    try:
        prices = [float(value) for value in history[-60:]]
        if len(prices) < 20:
            return "NORMAL"
        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent_changes = changes[-20:]
        baseline_changes = changes[:-5] or recent_changes
        sigma = max(_std(recent_changes), 0.01)
        baseline_sigma = max(_std(baseline_changes), 0.01)
        path = sum(abs(value) for value in recent_changes)
        net_move = abs(prices[-1] - prices[-min(21, len(prices))])
        efficiency = net_move / max(path, 0.01)
        range_sigma = (max(prices[-20:]) - min(prices[-20:])) / sigma
        velocity_z = abs(_mean(recent_changes[-3:])) / sigma
        volatility_ratio = sigma / baseline_sigma

        if volatility_ratio >= 1.8 and (velocity_z >= 1.1 or range_sigma >= 7.0):
            return "HIGH_VOL_OPEN"
        if efficiency >= 0.58 and range_sigma >= 4.5 and velocity_z >= 0.35:
            return "STRONG_TREND"
        if efficiency <= 0.18 and range_sigma <= 5.0:
            return "SIDEWAYS_CHOPPY"
        if volatility_ratio <= 0.55 and efficiency <= 0.30:
            return "LOW_ATR_DEAD"
        if volatility_ratio <= 0.85 and efficiency >= 0.30:
            return "SCALPING"
        return "NORMAL"
    except Exception:
        return "NORMAL"


def calculate_mtf_trend(h1_prices, h5_prices, m15_prices):
    try:
        if min(len(h1_prices), len(h5_prices), len(m15_prices)) < 2:
            return "NEUTRAL"
        trends = ["BULLISH" if values[-1] > values[-2] else "BEARISH" for values in (h1_prices, h5_prices, m15_prices)]
        return "STRONG_" + trends[0] if len(set(trends)) == 1 else "MIXED_CHOPS"
    except Exception:
        return "NEUTRAL"


def analyze_oi_buildup(oi_change, price_change):
    if oi_change > 0 and price_change > 0:
        return "LONG_BUILDUP"
    if oi_change > 0 and price_change < 0:
        return "SHORT_BUILDUP"
    if oi_change < 0 and price_change < 0:
        return "LONG_UNWINDING"
    if oi_change < 0 and price_change > 0:
        return "SHORT_COVERING"
    return "NO_NOISE"


def filter_iv_percentile(current_iv, iv_history):
    try:
        if len(iv_history) < 2 or max(iv_history) == min(iv_history):
            return 50.0
        return round((current_iv - min(iv_history)) / (max(iv_history) - min(iv_history)) * 100, 2)
    except Exception:
        return 50.0


def calculate_option_chain_imbalance(total_call_oi, total_put_oi):
    try:
        return round(float(total_put_oi) / float(total_call_oi), 2) if total_call_oi else 1.0
    except Exception:
        return 1.0


def get_ml_probability_score(history):
    try:
        if len(history) < 10:
            return 55.0
        features = [history[i] - history[i - 1] for i in range(1, len(history))]
        sign = lambda value: 1 if value > 0 else -1 if value < 0 else 0
        recent = [sign(value) for value in features[-3:]]
        matches = sum([sign(value) for value in features[i:i + 3]] == recent for i in range(len(features) - 3))
        probability = matches / (len(features) - 3) * 100 if len(features) > 3 else 50.0
        return round(max(35.0, min(95.0, probability + 35.0)), 2)
    except Exception:
        return 55.0


def adjust_threshold(regime, current_time_str):
    base = {
        "SIDEWAYS_CHOPPY": 12,
        "LOW_ATR_DEAD": 12,
        "STRONG_TREND": 8,
        "HIGH_VOL_OPEN": 9,
        "SCALPING": 9,
        "NORMAL": 10,
    }.get(regime, 10)
    if current_time_str >= "14:30":
        base += 1
    return base
