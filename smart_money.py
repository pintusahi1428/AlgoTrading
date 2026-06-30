import math


def _safe_prices(prices):
    return [float(value) for value in prices] if prices and len(prices) >= 3 else None


def _dynamic_move(prices, multiplier=0.5):
    changes = [float(prices[i]) - float(prices[i - 1]) for i in range(1, len(prices))]
    if len(changes) < 2:
        return 0.05
    avg = sum(changes) / len(changes)
    sigma = math.sqrt(sum((value - avg) ** 2 for value in changes) / len(changes))
    return max(0.05, sigma * multiplier)


def detect_bos(prices):
    try:
        prices = _safe_prices(prices)
        if not prices:
            return False
        lookback = min(10, len(prices) - 2)
        threshold = _dynamic_move(prices[-lookback - 2:], 0.5)
        return prices[-1] > max(prices[-lookback - 1:-1]) + threshold
    except Exception:
        return False


def detect_choch(prices):
    try:
        prices = _safe_prices(prices)
        if not prices:
            return False
        lookback = min(10, len(prices) - 2)
        threshold = _dynamic_move(prices[-lookback - 2:], 0.5)
        return prices[-1] < min(prices[-lookback - 1:-1]) - threshold
    except Exception:
        return False


def liquidity_sweep(prices):
    try:
        prices = _safe_prices(prices)
        if not prices or len(prices) < 5:
            return None
        window = prices[-min(25, len(prices)):-2]
        recent_high, recent_low = max(window), min(window)
        if max(prices[-2:]) > recent_high and prices[-1] < recent_high:
            return "BUY_SIDE_LIQUIDITY"
        if min(prices[-2:]) < recent_low and prices[-1] > recent_low:
            return "SELL_SIDE_LIQUIDITY"
        return None
    except Exception:
        return None


def strong_sweep_detection(prices):
    try:
        prices = _safe_prices(prices)
        if not prices or len(prices) < 12:
            return None
        window = prices[-12:-2]
        recent_high, recent_low = max(window), min(window)
        last, prev = prices[-1], prices[-2]
        threshold = _dynamic_move(prices[-12:], 1.4)
        if prev > recent_high and last < recent_high and abs(last - prev) >= threshold:
            return "STRONG_BUY_SIDE_SWEEP"
        if prev < recent_low and last > recent_low and abs(last - prev) >= threshold:
            return "STRONG_SELL_SIDE_SWEEP"
        return None
    except Exception:
        return None


def detect_order_block(prices):
    try:
        prices = _safe_prices(prices)
        if not prices:
            return None
        recent = prices[-min(10, len(prices)):]
        deviation = prices[-1] - (sum(recent) / len(recent))
        threshold = _dynamic_move(recent, 0.75)
        if deviation > threshold:
            return "BULLISH_OB"
        if deviation < -threshold:
            return "BEARISH_OB"
        return None
    except Exception:
        return None


def detect_fvg(prices):
    try:
        prices = _safe_prices(prices)
        if not prices or len(prices) < 5:
            return None
        prev_3, recent_3 = prices[-5:-2], prices[-3:]
        threshold = _dynamic_move(prices[-12:], 0.6)
        if min(recent_3) > max(prev_3) + threshold:
            return "BULLISH_FVG"
        if max(recent_3) < min(prev_3) - threshold:
            return "BEARISH_FVG"
        return None
    except Exception:
        return None


def smart_money_analysis(prices):
    return {
        "bos": detect_bos(prices),
        "choch": detect_choch(prices),
        "liquidity": liquidity_sweep(prices),
        "strong_sweep": strong_sweep_detection(prices),
        "order_block": detect_order_block(prices),
        "fvg": detect_fvg(prices),
    }


def directional_smc_score(prices):
    data = smart_money_analysis(prices)
    bullish = (
        (40 if data["bos"] else 0)
        + (25 if data["liquidity"] == "SELL_SIDE_LIQUIDITY" else 0)
        + (20 if data["strong_sweep"] == "STRONG_SELL_SIDE_SWEEP" else 0)
        + (25 if data["order_block"] == "BULLISH_OB" else 0)
        + (20 if data["fvg"] == "BULLISH_FVG" else 0)
    )
    bearish = (
        (40 if data["choch"] else 0)
        + (25 if data["liquidity"] == "BUY_SIDE_LIQUIDITY" else 0)
        + (20 if data["strong_sweep"] == "STRONG_BUY_SIDE_SWEEP" else 0)
        + (25 if data["order_block"] == "BEARISH_OB" else 0)
        + (20 if data["fvg"] == "BEARISH_FVG" else 0)
    )
    direction = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"
    return min(max(bullish, bearish), 100), direction, data


def smc_score(prices):
    try:
        if not _safe_prices(prices):
            return 0
        return directional_smc_score(prices)[0]
    except Exception:
        return 0
