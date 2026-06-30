import os
import sys

import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

from data_fetcher import DataFetcher
from token_manager import load_tokens_once


def status(label, available, value):
    marker = "PASS" if available else "BLOCKED"
    print(f"{marker:<7} {label:<22}: {value}")


def main():
    load_dotenv()
    if not load_tokens_once():
        raise RuntimeError("token_master.json could not be loaded")

    broker = SmartConnect(api_key=os.getenv("API_KEY"))
    session = broker.generateSession(
        os.getenv("CLIENT_ID"),
        os.getenv("PIN"),
        pyotp.TOTP(os.getenv("BROKER_TOTP_SECRET", "").strip()).now(),
    )
    if not session or not session.get("status"):
        raise RuntimeError(f"Angel login failed: {session}")

    spot_response = broker.ltpData("NSE", "NIFTY", "26000")
    spot = float(spot_response.get("data", {}).get("ltp", 0) or 0)
    if spot <= 0:
        raise RuntimeError(f"NIFTY spot unavailable: {spot_response}")

    fetcher = DataFetcher(broker)
    factors = fetcher.get_live_market_factors("NIFTY", spot)

    print("=" * 72)
    print("MASTER SNIPER VERIFIED LIVE FACTOR CHECK")
    print("=" * 72)
    print(f"NIFTY spot             : {spot:.2f}")
    status("Angel option IV", factors["iv_available"], factors["iv_percentile"])
    status("NSE IX Gift Nifty", factors["gift_nifty_available"], factors["gift_nifty"])
    status("NIFTY 50 A/D ratio", factors["ad_ratio_available"], factors["ad_ratio"])
    status("Top weighted stocks", factors["top_weighted_available"], factors["top_weighted_confirmation"])
    status("Real Greeks", factors["greeks_available"], f"{factors.get('greeks_bias')} D={factors.get('net_delta')}")
    status("Dealer Position", factors["dealer_position_available"], factors.get("dealer_position_signal"))
    status("Max Pain", factors["max_pain_available"], f"{factors.get('max_pain')} {factors.get('max_pain_bias')}")
    status("Top-15 Breadth", factors["top15_weighted_available"], f"{factors.get('top15_weighted_confirmation')} {factors.get('top15_weighted_score')}")
    status("Angel Best-5 Depth", factors["order_book_imbalance_available"], f"{factors.get('order_book_direction')} {factors.get('order_book_imbalance')}")
    print("-" * 72)
    if factors["missing"]:
        print("Unavailable optional factors:")
        for item in factors["missing"]:
            print(f"  - {item}")
    else:
        print("All configured live factors are available.")
    print("=" * 72)

    required = (
        factors["iv_available"],
        factors["gift_nifty_available"],
        factors["ad_ratio_available"],
        factors["top_weighted_available"],
        factors["greeks_available"],
        factors["dealer_position_available"],
        factors["max_pain_available"],
        factors["top15_weighted_available"],
        factors["order_book_imbalance_available"],
    )
    return 0 if all(required) else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"LIVE FACTOR TEST FAILED: {exc}")
        sys.exit(1)
