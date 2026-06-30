from pathlib import Path

p = Path("data_fetcher.py")
txt = p.read_text(encoding="utf-8")

marker = "# ===== NSE OPTION CHAIN FALLBACK V3 - CMD FIX ====="
if marker in txt:
    print("NSE fallback v3 already installed.")
    raise SystemExit

patch = r'''
# ===== NSE OPTION CHAIN FALLBACK V3 - CMD FIX =====
def _nse_chain_fallback_v3(self, index, atm_strike, depth):
    import requests
    import time

    symbol = str(index).upper()
    step = 50 if symbol == "NIFTY" else 100
    strikes = [atm_strike + (i * step) for i in range(-depth, depth + 1)]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.nseindia.com/option-chain?symbol={symbol}",
        "Connection": "keep-alive",
    }

    api_urls = [
        f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
        f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}&instrument=OPTIDX",
    ]

    last_error = None
    for attempt in range(5):
        for api_url in api_urls:
            try:
                session = requests.Session()
                warm = session.get(f"https://www.nseindia.com/option-chain?symbol={symbol}", headers=headers, timeout=(5, 15), stream=True)
                warm.close()
                time.sleep(0.5)

                res = session.get(api_url, headers=headers, timeout=(5, 25))
                print("NSE option-chain attempt", attempt + 1, res.status_code, len(res.content), api_url)

                if res.status_code != 200:
                    last_error = f"HTTP {res.status_code}: {res.text[:120]}"
                    continue

                data = res.json()
                records = data.get("records", {}).get("data", [])
                if not records:
                    last_error = "blank records"
                    continue

                rows = []
                for item in records:
                    strike = int(float(item.get("strikePrice", 0) or 0))
                    if strike not in strikes:
                        continue
                    ce = item.get("CE", {}) or {}
                    pe = item.get("PE", {}) or {}
                    rows.append({
                        "strike": strike,
                        "call_oi": float(ce.get("openInterest", 0) or 0),
                        "put_oi": float(pe.get("openInterest", 0) or 0),
                        "call_coi": float(ce.get("changeinOpenInterest", 0) or 0),
                        "put_coi": float(pe.get("changeinOpenInterest", 0) or 0),
                        "call_iv": float(ce.get("impliedVolatility", 0) or 0),
                        "put_iv": float(pe.get("impliedVolatility", 0) or 0),
                        "call_volume": float(ce.get("totalTradedVolume", 0) or 0),
                        "put_volume": float(pe.get("totalTradedVolume", 0) or 0),
                    })

                if rows:
                    return sorted(rows, key=lambda x: x["strike"])

                last_error = "no ATM rows"
            except Exception as exc:
                last_error = exc
                time.sleep(1.5)

    raise RuntimeError(f"NSE option chain fallback v3 failed: {last_error}")

DataFetcher._fetch_option_chain_nse = _nse_chain_fallback_v3
# ===== END NSE OPTION CHAIN FALLBACK V3 =====
'''
p.write_text(txt + "\n" + patch + "\n", encoding="utf-8")
print("NSE fallback v3 installed in data_fetcher.py")
