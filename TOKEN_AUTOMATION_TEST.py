import os

from dotenv import load_dotenv

from token_manager import get_atm_option, get_current_expiry, get_option_lot_size, load_tokens_once


def main():
    load_dotenv()
    if not load_tokens_once():
        raise RuntimeError("Token master could not be loaded")
    reference_spot = float(os.getenv("TOKEN_TEST_REFERENCE_SPOT", "24000"))
    for option_type in ("CE", "PE"):
        symbol, token = get_atm_option("NIFTY", reference_spot, option_type)
        lot_size = get_option_lot_size(symbol, token)
        if not symbol or not token or not lot_size or lot_size <= 0:
            raise AssertionError(f"Automatic {option_type} token/lot resolution failed")
        print(f"PASS: {option_type} {symbol} token={token} exchange_lot={lot_size}")
    print(f"PASS: Nearest token-master expiry={get_current_expiry('NIFTY')}")
    print("\nTOKEN AUTOMATION TEST RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
