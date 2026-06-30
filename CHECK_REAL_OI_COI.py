import os

import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

from data_fetcher import DataFetcher


def main():
    load_dotenv()
    obj = SmartConnect(api_key=os.getenv("API_KEY"))
    obj.generateSession(
        os.getenv("CLIENT_ID"),
        os.getenv("PIN"),
        pyotp.TOTP(os.getenv("BROKER_TOTP_SECRET").strip()).now(),
    )
    fetcher = DataFetcher(obj)
    spot_res = fetcher.fetch_ltp_with_retry("NSE", "NIFTY", "26000")
    if not spot_res.get("success"):
        raise SystemExit(f"NIFTY spot failed: {spot_res}")
    spot = float(spot_res["data"]["ltp"])
    factors = fetcher.get_live_market_factors("NIFTY", spot)
    print("REAL OI/COI CHECK")
    print(f"NIFTY spot          : {spot}")
    print(f"Chain available     : {factors.get('chain_available')}")
    print(f"PCR/OI imbalance    : {factors.get('chain_imbalance')}")
    print(f"Call COI            : {factors.get('call_coi')}")
    print(f"Put COI             : {factors.get('put_coi')}")
    print(f"COI imbalance       : {factors.get('coi_imbalance')}")
    print(f"COI signal          : {factors.get('coi_signal')}")
    print(f"IV available        : {factors.get('iv_available')}")
    print(f"IV percentile       : {factors.get('iv_percentile')}")
    print(f"India VIX available : {factors.get('india_vix_available')}")
    print(f"India VIX           : {factors.get('india_vix')}")
    if factors.get("missing"):
        print("Missing/blocked     :")
        for item in factors["missing"]:
            print(f"  - {item}")


if __name__ == "__main__":
    main()
