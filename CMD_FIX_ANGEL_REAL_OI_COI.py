from pathlib import Path

p = Path("data_fetcher.py")
txt = p.read_text(encoding="utf-8")

marker = "# ===== ANGEL REAL OI COI FALLBACK - CMD FIX ====="
if marker in txt:
    print("Angel real OI/COI fallback already installed.")
    raise SystemExit

patch = r'''
# ===== ANGEL REAL OI COI FALLBACK - CMD FIX =====
def _angel_marketdata_option_chain(self, index, spot_price, depth=3):
    import json
    import os
    import re
    from pathlib import Path

    from token_manager import load_tokens_once, get_current_expiry
    import token_manager

    index = str(index).upper()
    step = 50 if index == "NIFTY" else 100
    atm = int(round(float(spot_price) / step) * step)
    expiry = get_current_expiry(index)

    load_tokens_once()
    master = getattr(token_manager, "_MASTER_MAP", {})

    wanted = {}
    for i in range(-depth, depth + 1):
        strike = atm + (i * step)
        for opt_type in ("CE", "PE"):
            symbol = f"{index}{expiry}{strike}{opt_type}"
            token = master.get(symbol)
            if token:
                wanted[str(token)] = {"symbol": symbol, "strike": strike, "type": opt_type}

    if not wanted:
        raise RuntimeError("Angel OI fallback could not map option tokens")

    res = self.broker.getMarketData("FULL", {"NFO": list(wanted.keys())})
    if not res or not res.get("status"):
        raise RuntimeError(f"Angel getMarketData FULL failed: {res}")

    fetched = res.get("data", {}).get("fetched", [])
    if not fetched:
        raise RuntimeError("Angel getMarketData FULL returned blank fetched list")

    os.makedirs("logs", exist_ok=True)
    snap_path = Path("logs/oi_snapshot.json")
    try:
        previous = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else {}
    except Exception:
        previous = {}

    current_snapshot = dict(previous)
    rows_by_strike = {}

    for item in fetched:
        token = str(item.get("symbolToken", item.get("symboltoken", "")))
        meta = wanted.get(token)
        if not meta:
            symbol = str(item.get("tradingSymbol", item.get("tradingsymbol", "")))
            m = re.search(r"(\d+)(CE|PE)$", symbol)
            if not m:
                continue
            strike = int(m.group(1))
            opt_type = m.group(2)
        else:
            symbol = meta["symbol"]
            strike = meta["strike"]
            opt_type = meta["type"]

        oi = float(item.get("opnInterest", item.get("openInterest", item.get("oi", 0))) or 0)
        prev_oi = float(previous.get(symbol, oi))
        coi = oi - prev_oi
        current_snapshot[symbol] = oi

        row = rows_by_strike.setdefault(strike, {"strike": strike})
        if opt_type == "CE":
            row["call_oi"] = oi
            row["call_coi"] = coi
            row["call_volume"] = float(item.get("tradeVolume", 0) or 0)
        else:
            row["put_oi"] = oi
            row["put_coi"] = coi
            row["put_volume"] = float(item.get("tradeVolume", 0) or 0)

    snap_path.write_text(json.dumps(current_snapshot, indent=2), encoding="utf-8")

    rows = []
    for strike in sorted(rows_by_strike):
        row = rows_by_strike[strike]
        row.setdefault("call_oi", 0.0)
        row.setdefault("put_oi", 0.0)
        row.setdefault("call_coi", 0.0)
        row.setdefault("put_coi", 0.0)
        row.setdefault("call_iv", 0.0)
        row.setdefault("put_iv", 0.0)
        row.setdefault("call_volume", 0.0)
        row.setdefault("put_volume", 0.0)
        rows.append(row)

    if not rows:
        raise RuntimeError("Angel real OI fallback produced no rows")
    return rows


_old_fetch_option_chain_primary = DataFetcher._fetch_option_chain_primary

def _fetch_option_chain_primary_real_oi(self, index, atm_strike, depth):
    try:
        return _angel_marketdata_option_chain(self, index, atm_strike, depth)
    except Exception as exc:
        logger.error(f"Angel real OI/COI fallback failed: {exc}")
    try:
        return _old_fetch_option_chain_primary(self, index, atm_strike, depth)
    except Exception:
        return []


DataFetcher._fetch_option_chain_primary = _fetch_option_chain_primary_real_oi
# ===== END ANGEL REAL OI COI FALLBACK =====
'''
p.write_text(txt + "\n" + patch + "\n", encoding="utf-8")
print("Angel real OI/COI fallback installed in data_fetcher.py")
