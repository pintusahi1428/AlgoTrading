import datetime as dt
import json
import os
import re
import sys
import time

import requests


TOKEN_FILE = "token_master.json"
TOKEN_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
_MASTER_MAP = {}
_OPTION_ROWS = []


def _parse_expiry(value):
    text = str(value or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _expiry_from_symbol(symbol):
    match = re.search(r"(NIFTY|BANKNIFTY)(\d{1,2}[A-Z]{3}\d{2,4})\d+(CE|PE)$", str(symbol).upper())
    return _parse_expiry(match.group(2)) if match else None


def load_tokens_once(force_download=False):
    global _MASTER_MAP, _OPTION_ROWS
    file_exists = os.path.exists(TOKEN_FILE)
    if force_download or not file_exists or os.path.getsize(TOKEN_FILE) < 5_000_000:
        try:
            print("Downloading Angel One token master...")
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            with requests.get(TOKEN_URL, headers=headers, timeout=30, stream=True) as res:
                if res.status_code != 200:
                    print(f"Token download failed: HTTP {res.status_code}")
                    return False
                total = 0
                with open(TOKEN_FILE, "wb") as fh:
                    for chunk in res.iter_content(chunk_size=1024 * 512):
                        if chunk:
                            fh.write(chunk)
                            total += len(chunk)
                            sys.stdout.write(f"\rToken master: {total / (1024 * 1024):.2f} MB")
                            sys.stdout.flush()
                print()
                if total < 5_000_000:
                    print("Token master download is too small; refusing to use it.")
                    return False
        except Exception as exc:
            print(f"Token master download error: {exc}")
            return False

    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("data", []) if isinstance(data, dict) else data
        _OPTION_ROWS = [
            row for row in rows
            if row.get("exch_seg") == "NFO" and row.get("instrumenttype") == "OPTIDX"
        ]
        _MASTER_MAP = {row.get("symbol"): str(row.get("token")) for row in _OPTION_ROWS}
        print(f"Loaded {len(_MASTER_MAP)} option tokens.")
        return bool(_MASTER_MAP)
    except Exception as exc:
        print(f"Token master load error: {exc}")
        return False


def get_atm_option(index_name, spot_price, option_type, allow_refresh=True):
    index = str(index_name).upper()
    opt_type = str(option_type).upper()
    step = 50 if index == "NIFTY" else 100
    strike = int(round(float(spot_price) / step) * step)
    today = dt.date.today()

    matches = []
    fallback_matches = []
    for row in _OPTION_ROWS:
        symbol = str(row.get("symbol", "")).upper()
        row_name = str(row.get("name", "")).upper()
        if row_name and row_name != index:
            continue
        if not symbol.startswith(index):
            continue
        if not symbol.endswith(opt_type):
            continue
        try:
            row_strike = int(float(row.get("strike", 0)) / 100)
        except Exception:
            row_strike = None
        expiry = _parse_expiry(row.get("expiry")) or _expiry_from_symbol(symbol)
        if not expiry or expiry < today:
            continue
        if row_strike == strike or str(strike) in symbol:
            matches.append((expiry, 0, symbol, str(row.get("token"))))
        elif row_strike:
            fallback_matches.append((expiry, abs(row_strike - strike), symbol, str(row.get("token"))))

    if not matches and fallback_matches:
        matches = fallback_matches
    if not matches:
        if allow_refresh and load_tokens_once(force_download=True):
            return get_atm_option(index_name, spot_price, option_type, allow_refresh=False)
        return None, None
    matches.sort(key=lambda item: (item[0], item[1]))
    return matches[0][2], matches[0][3]


def get_option_lot_size(symbol, token=None, fallback=None):
    target_symbol = str(symbol or "").strip().upper()
    target_token = str(token or "").strip()
    for row in _OPTION_ROWS:
        row_symbol = str(row.get("symbol", "")).strip().upper()
        row_token = str(row.get("token", "")).strip()
        if row_symbol == target_symbol or (target_token and row_token == target_token):
            try:
                lot_size = int(float(row.get("lotsize", 0) or 0))
                if lot_size > 0:
                    return lot_size
            except Exception:
                pass
    if fallback is not None:
        return int(fallback)
    return None


def get_nearest_expiry_date(index_name):
    index = str(index_name).upper()
    today = dt.date.today()
    expiries = []
    for row in _OPTION_ROWS:
        if str(row.get("name", "")).upper() != index:
            continue
        expiry = _parse_expiry(row.get("expiry")) or _expiry_from_symbol(row.get("symbol"))
        if expiry and expiry >= today:
            expiries.append(expiry)
    return min(expiries) if expiries else None


def wait_for_fresh_tokens():
    for _ in range(3):
        if load_tokens_once():
            return True
        time.sleep(2)
    return False



# ===== EXPIRY HELPER - CMD FIX =====
def get_current_expiry(index_name):
    import datetime

    today = datetime.date.today()
    index_name = str(index_name).upper()

    actual_expiries = []
    for row in _OPTION_ROWS:
        if str(row.get("name", "")).upper() != index_name:
            continue
        expiry = _parse_expiry(row.get("expiry")) or _expiry_from_symbol(row.get("symbol"))
        if expiry and expiry >= today:
            actual_expiries.append(expiry)
    if actual_expiries:
        target_date = min(actual_expiries)
        return f"{target_date.day}{target_date.strftime('%b').upper()}{target_date.strftime('%y')}"

    if index_name == "NIFTY":
        days_ahead = (1 - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = today + datetime.timedelta(days=days_ahead)
    elif index_name == "BANKNIFTY":
        year = today.year
        month = today.month
        next_month = today.replace(day=28) + datetime.timedelta(days=4)
        last_day = next_month - datetime.timedelta(days=next_month.day)
        offset = (last_day.weekday() - 2) % 7
        target_date = last_day - datetime.timedelta(days=offset)
        if today > target_date:
            month = month + 1 if month < 12 else 1
            year = year if month > 1 else year + 1
            if month == 12:
                last_day = datetime.date(year, 12, 31)
            else:
                last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
            offset = (last_day.weekday() - 2) % 7
            target_date = last_day - datetime.timedelta(days=offset)
    else:
        days_ahead = (3 - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = today + datetime.timedelta(days=days_ahead)

    day = target_date.strftime("%d").lstrip("0")
    month = target_date.strftime("%b").upper()
    year = target_date.strftime("%y")
    return f"{day}{month}{year}"
# ===== END EXPIRY HELPER =====

