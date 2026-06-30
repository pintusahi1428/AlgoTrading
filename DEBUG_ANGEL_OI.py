import os, json, pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect
from token_manager import load_tokens_once, get_current_expiry
import token_manager

load_dotenv()
load_tokens_once()

obj = SmartConnect(api_key=os.getenv("API_KEY"))
s = obj.generateSession(os.getenv("CLIENT_ID"), os.getenv("PIN"), pyotp.TOTP(os.getenv("BROKER_TOTP_SECRET").strip()).now())
print("LOGIN", s.get("status"))

spot = float(obj.ltpData("NSE", "NIFTY", "26000")["data"]["ltp"])
print("SPOT", spot)

index = "NIFTY"
step = 50
atm = int(round(spot / step) * step)
expiry = get_current_expiry(index)
print("EXPIRY", expiry, "ATM", atm)

wanted = {}
for i in range(-1, 2):
    strike = atm + (i * step)
    for opt_type in ("CE", "PE"):
        symbol = f"{index}{expiry}{strike}{opt_type}"
        token = token_manager._MASTER_MAP.get(symbol)
        print("MAP", symbol, token)
        if token:
            wanted[str(token)] = symbol

print("TOKENS", wanted)

if not wanted:
    raise SystemExit("NO TOKENS MAPPED")

r = obj.getMarketData("FULL", {"NFO": list(wanted.keys())})
print(json.dumps(r, indent=2))
