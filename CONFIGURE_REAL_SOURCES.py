import json
import os
from pathlib import Path


TOKEN_FILE = Path("token_master.json")
ENV_FILE = Path(".env")

TOP_NIFTY_NAMES = [
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "TCS",
    "LT",
    "ITC",
    "SBIN",
    "AXISBANK",
    "BHARTIARTL",
]


def load_tokens():
    with TOKEN_FILE.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("data", [])
    return data if isinstance(data, list) else []


def upsert_env(values):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    existing = {}
    order = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            existing[key] = line
            order.append(key)
    for key, value in values.items():
        existing[key] = f"{key}={value}"
        if key not in order:
            order.append(key)
    output = []
    used = set()
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in existing:
                output.append(existing[key])
                used.add(key)
            else:
                output.append(line)
        else:
            output.append(line)
    for key in order:
        if key not in used:
            output.append(existing[key])
    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


def find_india_vix(tokens):
    for row in tokens:
        if row.get("exch_seg") == "NSE" and "VIX" in str(row).upper():
            return str(row.get("token", "")).strip()
    return ""


def find_top_tokens(tokens):
    found = []
    for name in TOP_NIFTY_NAMES:
        match = None
        for row in tokens:
            if row.get("exch_seg") != "NSE":
                continue
            row_name = str(row.get("name", "")).upper()
            row_symbol = str(row.get("symbol", "")).upper()
            if row_name == name or row_symbol == name or row_symbol == f"{name}-EQ":
                match = row
                break
        if match:
            found.append((str(match.get("symbol", name)), str(match.get("token", ""))))
    return found


def main():
    if not TOKEN_FILE.exists():
        raise SystemExit("token_master.json not found. Run token download first.")
    tokens = load_tokens()
    india_vix = find_india_vix(tokens)
    top = find_top_tokens(tokens)
    values = {}
    if india_vix:
        values["INDIA_VIX_TOKEN"] = india_vix
    if top:
        values["TOP_NIFTY_SYMBOLS"] = ",".join(symbol for symbol, _ in top)
        values["TOP_NIFTY_TOKENS"] = ",".join(token for _, token in top)
        values["TOP_NIFTY_WEIGHTS"] = ",".join(["1"] * len(top))
    upsert_env(values)
    print("REAL SOURCE CONFIG UPDATE")
    print(f"India VIX token : {india_vix or 'NOT FOUND'}")
    print(f"Top Nifty tokens: {len(top)} found")
    for symbol, token in top:
        print(f"  {symbol:<16} {token}")
    print("\n.env updated. TOP_NIFTY_WEIGHTS uses equal confirmation weights until you provide official weights.")
    print("COI and COI imbalance are pulled from real NSE option-chain fallback at runtime.")


if __name__ == "__main__":
    main()
