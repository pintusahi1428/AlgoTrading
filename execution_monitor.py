import json
import math
import os
import time


class ExecutionQualityMonitor:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        self.path = os.path.join(log_dir, "execution_quality.jsonl")
        self._last_missed_key = None
        self._last_missed_at = 0.0

    def _append(self, payload):
        os.makedirs(self.log_dir, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")

    def record_fill(self, symbol, side, order_kind, reference_price, fill_price, decision_epoch, send_epoch, response_epoch, fill_epoch, order_id):
        reference = float(reference_price)
        fill = float(fill_price)
        adverse = fill - reference if side == "BUY" else reference - fill
        slippage_bps = (adverse / reference * 10000.0) if reference > 0 else 0.0
        latency_ms = max(0.0, (fill_epoch - float(decision_epoch or send_epoch)) * 1000.0)
        response_ms = max(0.0, (response_epoch - send_epoch) * 1000.0)
        if adverse <= float(os.getenv("EXECUTION_EXCELLENT_SLIPPAGE_POINTS", "0.25")):
            quality = "EXCELLENT"
        elif adverse <= float(os.getenv("EXECUTION_GOOD_SLIPPAGE_POINTS", "0.75")):
            quality = "GOOD"
        elif adverse <= float(os.getenv("EXECUTION_POOR_SLIPPAGE_POINTS", "1.50")):
            quality = "FAIR"
        else:
            quality = "POOR"
        payload = {
            "event": "FILL",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "side": side,
            "order_kind": order_kind,
            "order_id": str(order_id or ""),
            "reference_price": reference,
            "fill_price": fill,
            "adverse_slippage_points": round(adverse, 4),
            "slippage_bps": round(slippage_bps, 4),
            "total_latency_ms": round(latency_ms, 2),
            "broker_response_ms": round(response_ms, 2),
            "fill_quality": quality,
        }
        self._append(payload)
        return payload

    def record_missed(self, reason, signal=None, context=None):
        signal = signal or {}
        context = context or {}
        key = (str(reason), signal.get("candidate_side"), signal.get("confidence"))
        now = time.time()
        if key == self._last_missed_key and now - self._last_missed_at < 15.0:
            return
        self._last_missed_key = key
        self._last_missed_at = now
        self._append({
            "event": "MISSED_TRADE",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": str(reason),
            "candidate": signal.get("candidate_side", "HOLD"),
            "score": signal.get("confidence", 0),
            "required": signal.get("required", 0),
            "match_percent": signal.get("match_percent", 0.0),
            "day_type": context.get("day_type", "UNKNOWN"),
        })

    def summary(self, limit=500):
        if not os.path.exists(self.path):
            return {"fills": 0, "missed": 0, "avg_slippage": 0.0, "avg_latency_ms": 0.0, "rating": "NO_DATA"}
        rows = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            return {"fills": 0, "missed": 0, "avg_slippage": 0.0, "avg_latency_ms": 0.0, "rating": "ERROR"}
        rows = rows[-limit:]
        fills = [row for row in rows if row.get("event") == "FILL"]
        missed = [row for row in rows if row.get("event") == "MISSED_TRADE"]
        avg_slippage = sum(float(row.get("adverse_slippage_points", 0.0)) for row in fills) / len(fills) if fills else 0.0
        avg_latency = sum(float(row.get("total_latency_ms", 0.0)) for row in fills) / len(fills) if fills else 0.0
        poor_rate = sum(row.get("fill_quality") == "POOR" for row in fills) / len(fills) if fills else 0.0
        if len(fills) < 10:
            rating = "INSUFFICIENT_DATA"
        elif avg_slippage <= 0.5 and avg_latency <= 1500 and poor_rate <= 0.10:
            rating = "A"
        elif avg_slippage <= 1.0 and avg_latency <= 3000 and poor_rate <= 0.20:
            rating = "B"
        elif avg_slippage <= 2.0 and poor_rate <= 0.35:
            rating = "C"
        else:
            rating = "D"
        return {
            "fills": len(fills),
            "missed": len(missed),
            "avg_slippage": round(avg_slippage, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "poor_fill_rate": round(poor_rate, 4),
            "rating": rating,
        }
