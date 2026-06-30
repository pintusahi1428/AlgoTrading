import math
import os
import time
from datetime import datetime

import requests
from logger import logger


class DataFetcher:
    def __init__(self, broker, session_refresher=None):
        self.broker = broker
        self.session_refresher = session_refresher
        self.cache_ttl_seconds = 300
        self._cache = {}
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        })

    def set_broker(self, broker):
        self.broker = broker

    def _cache_set(self, key, value):
        self._cache[key] = {"ts": time.time(), "value": value}

    def _cache_get(self, key, ttl=None):
        item = self._cache.get(key)
        if not item:
            return None
        max_age = self.cache_ttl_seconds if ttl is None else float(ttl)
        if time.time() - item["ts"] > max_age:
            return None
        return item["value"]

    def _looks_like_auth_error(self, payload):
        text = str(payload).upper()
        return "TOKEN" in text or "AG8003" in text or "SESSION" in text or "AUTH" in text

    def fetch_ltp_with_retry(self, exchange, symbol, token, retries=6, delay=0.35):
        last_error = None
        for attempt in range(max(1, retries)):
            try:
                res = self.broker.ltpData(str(exchange), str(symbol), str(token))
                if res and (res.get("status") or res.get("success")) and res.get("data"):
                    ltp = float(res["data"].get("ltp", 0))
                    if ltp > 0:
                        return {
                            "success": True,
                            "status": True,
                            "data": {
                                "ltp": ltp,
                                "volume": float(res["data"].get("volume") or 0),
                            },
                            "raw": res,
                        }
                last_error = res
                if self.session_refresher and self._looks_like_auth_error(res):
                    self.broker = self.session_refresher()
            except Exception as exc:
                last_error = exc
                logger.error(f"LTP fetch failed for {symbol}/{token}: {exc}")
                if self.session_refresher and self._looks_like_auth_error(exc):
                    self.broker = self.session_refresher()
            time.sleep(delay * (1 + attempt * 0.35))
        return {"success": False, "status": False, "message": str(last_error)}

    def get_ad_ratio(self):
        cached = self._cache_get("nse:nifty50_ad_ratio", ttl=60)
        if cached is not None:
            return cached

        url = os.getenv("AD_RATIO_URL", "").strip() or "https://www.nseindia.com/api/allIndices"
        try:
            headers = {
                "Referer": "https://www.nseindia.com/market-data/live-equity-market?key=NIFTY%2050",
                "Accept-Language": "en-US,en;q=0.9",
            }
            self.http.get("https://www.nseindia.com", headers=headers, timeout=(3.5, 8.0))
            urls = [url]
            equity_url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
            if equity_url not in urls:
                urls.append(equity_url)

            for source_url in urls:
                try:
                    res = self.http.get(source_url, headers=headers, timeout=(3.5, 8.0))
                    res.raise_for_status()
                    data = res.json()
                    if not isinstance(data, dict):
                        continue
                    if "ad_ratio" in data:
                        ratio = float(data["ad_ratio"])
                        self._cache_set("nse:nifty50_ad_ratio", ratio)
                        return ratio

                    rows = data.get("data", [])
                    if not isinstance(rows, list):
                        rows = []
                    nifty = next(
                        (row for row in rows if str(row.get("index", row.get("indexName", ""))).upper() == "NIFTY 50"),
                        None,
                    )
                    if nifty:
                        adv = float(nifty.get("advances", nifty.get("advance", 0)) or 0)
                        dec = float(nifty.get("declines", nifty.get("decline", 0)) or 0)
                        if dec > 0:
                            ratio = round(adv / dec, 2)
                            self._cache_set("nse:nifty50_ad_ratio", ratio)
                            return ratio

                    constituents = [
                        row for row in rows
                        if str(row.get("symbol", "")).upper() not in ("", "NIFTY 50")
                    ]
                    advances = sum(1 for row in constituents if self._read_number(row, ["pChange", "percentChange", "change"]) > 0)
                    declines = sum(1 for row in constituents if self._read_number(row, ["pChange", "percentChange", "change"]) < 0)
                    if declines > 0 and advances + declines >= 20:
                        ratio = round(advances / declines, 2)
                        self._cache_set("nse:nifty50_ad_ratio", ratio)
                        return ratio

                    adv = float(data.get("advances", 0) or 0)
                    dec = float(data.get("declines", 0) or 0)
                    if dec > 0:
                        ratio = round(adv / dec, 2)
                        self._cache_set("nse:nifty50_ad_ratio", ratio)
                        return ratio
                except Exception as source_exc:
                    logger.warning(f"NSE A/D endpoint failed ({source_url}): {source_exc}")
        except Exception as exc:
            logger.error(f"AD ratio source failed: {exc}")
        return None

    def get_gift_nifty_sentiment(self):
        cached = self._cache_get("nseix:gift_nifty_sentiment", ttl=30)
        if cached is not None:
            return cached

        url = os.getenv("GIFT_NIFTY_URL", "").strip()
        try:
            if url and "nseix.com" not in url.lower():
                res = self.http.get(url, timeout=(3.5, 8.0))
                res.raise_for_status()
                data = res.json()
            else:
                token = self._cache_get("nseix:bearer_token", ttl=240)
                if not token:
                    token_res = self.http.get(
                        "https://www.nseix.com/api/generate-token",
                        timeout=(3.5, 10.0),
                    )
                    token_res.raise_for_status()
                    token = str(token_res.json().get("token", "")).strip()
                    if token:
                        self._cache_set("nseix:bearer_token", token)
                if not token:
                    raise RuntimeError("NSE IX token was blank")

                live_urls = [url] if url else []
                for fallback_url in (
                    "https://www.nseix.com/api/niccl-graph-headers",
                    "https://www.nseix.com/api/derivatives-watch?inst_type1=IDX&inst_type2=STK&type=live",
                ):
                    if fallback_url not in live_urls:
                        live_urls.append(fallback_url)
                rows = []
                for live_url in live_urls:
                    try:
                        res = self.http.get(
                            live_url,
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=(3.5, 10.0),
                        )
                        res.raise_for_status()
                        payload = res.json()
                        rows = payload.get("data", []) if isinstance(payload, dict) else []
                        if rows:
                            break
                    except Exception as endpoint_exc:
                        logger.warning(f"NSE IX endpoint failed ({live_url}): {endpoint_exc}")
                row = next(
                    (
                        item for item in rows
                        if str(item.get("UNDERLYING", item.get("SYMBOL", ""))).upper() == "NIFTY"
                        and str(item.get("INSTRUMENTTYPE", "")).upper() in ("FUTIDX", "INDEX FUTURES")
                    ),
                    rows[0] if rows else None,
                )
                if not row:
                    raise RuntimeError("NSE IX returned no near-month GIFT Nifty future")
                timestamp_text = str(row.get("TIMESTMP", "")).strip()
                if timestamp_text:
                    exchange_time = datetime.strptime(timestamp_text, "%d-%b-%Y %H:%M:%S")
                    now = datetime.now()
                    freshness_required = now.weekday() < 5 and (8, 45) <= (now.hour, now.minute) <= (15, 30)
                    if freshness_required and abs((now - exchange_time).total_seconds()) > 900:
                        raise RuntimeError(f"NSE IX quote is stale: {timestamp_text}")
                data = {
                    "change_percent": row.get("PERCHANGE", 0),
                    "change": row.get("DAYCHANGE", row.get("CHANGE", 0)),
                }

            if isinstance(data, dict):
                value = str(data.get("sentiment", data.get("trend", ""))).upper()
                if value in ("BULLISH", "BEARISH", "NEUTRAL"):
                    self._cache_set("nseix:gift_nifty_sentiment", value)
                    return value
                change = float(data.get("change", data.get("change_percent", 0)))
                value = "BULLISH" if change > 0 else "BEARISH" if change < 0 else "NEUTRAL"
                self._cache_set("nseix:gift_nifty_sentiment", value)
                return value
        except Exception as exc:
            logger.error(f"Gift Nifty source failed: {exc}")
        return None

    def get_option_chain(self, index, spot_price, depth=3):
        depth = max(8, int(depth))
        step = 50 if str(index).upper() == "NIFTY" else 100
        atm_strike = int(round(float(spot_price) / step) * step)
        cache_key = f"chain:{str(index).upper()}:{atm_strike}"
        cached = self._cache_get(cache_key, ttl=5)
        if cached:
            return cached
        chain = self._fetch_option_chain_primary(index, atm_strike, depth)
        if chain:
            self._cache_set(cache_key, chain)
            return chain
        cached = self._cache_get(cache_key)
        if cached:
            logger.warning(f"Using recent real option-chain cache for {index} {atm_strike}")
            return cached
        return []

    def _fetch_option_chain_primary(self, index, atm_strike, depth):
        if hasattr(self.broker, "optionChain"):
            try:
                res = self.broker.optionChain(str(index).upper(), str(atm_strike))
                if res and res.get("status") and isinstance(res.get("data"), list):
                    return res["data"][: max(1, depth * 2 + 1)]
            except Exception as exc:
                logger.error(f"Primary optionChain failed: {exc}")
        try:
            return self._fetch_option_chain_nse(index, atm_strike, depth)
        except Exception as exc:
            logger.error(f"NSE option-chain fallback failed: {exc}")
        return []

    def _fetch_option_chain_nse(self, index, atm_strike, depth):
        symbol = str(index).upper()
        if symbol == "BANKNIFTY":
            symbol = "BANKNIFTY"
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.nseindia.com/option-chain?symbol={symbol}",
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=4)
        res = session.get(url, headers=headers, timeout=5)
        data = res.json()
        records = data.get("records", {}).get("data", [])
        if not records:
            raise RuntimeError("NSE option chain returned blank records")
        step = 50 if str(index).upper() == "NIFTY" else 100
        strikes = [atm_strike + (i * step) for i in range(-depth, depth + 1)]
        rows = []
        for item in records:
            strike = int(float(item.get("strikePrice", 0)))
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
        if not rows:
            raise RuntimeError("NSE option chain had no ATM depth rows")
        return sorted(rows, key=lambda row: row["strike"])

    def _read_number(self, row, keys):
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                try:
                    return float(row.get(key))
                except Exception:
                    pass
        return 0.0

    def get_live_chain_imbalance(self, index, spot_price):
        chain = self.get_option_chain(index, spot_price)
        if not chain:
            raise RuntimeError("Option chain unavailable")
        call_oi = sum(self._read_number(x, ["call_oi", "ce_oi", "CE_OI", "callOI", "openInterest"]) for x in chain)
        put_oi = sum(self._read_number(x, ["put_oi", "pe_oi", "PE_OI", "putOI", "openInterest"]) for x in chain)
        if call_oi <= 0:
            raise RuntimeError("Call OI unavailable")
        return round(put_oi / call_oi, 2)

    def get_live_iv_percentile(self, index, spot_price):
        index = str(index).upper()
        step = 50 if index == "NIFTY" else 100
        atm = int(round(float(spot_price) / step) * step)
        cache_key = f"angel:iv_percentile:{index}:{atm}"
        cached = self._cache_get(cache_key, ttl=60)
        if cached is not None:
            return cached

        try:
            import token_manager

            today = datetime.now().date()
            expiries = []
            for row in getattr(token_manager, "_OPTION_ROWS", []):
                if str(row.get("name", "")).upper() != index:
                    continue
                expiry = token_manager._parse_expiry(row.get("expiry"))
                if expiry and expiry >= today:
                    expiries.append(expiry)
            if not expiries:
                raise RuntimeError("No live option expiry found for Angel Greeks")
            expiry_text = min(expiries).strftime("%d%b%Y").upper()

            response = self.broker.optionGreek({"name": index, "expirydate": expiry_text})
            if not response or not response.get("status") or not isinstance(response.get("data"), list):
                raise RuntimeError(f"Angel optionGreek failed: {response}")

            nearby = []
            atm_ivs = []
            for row in response["data"]:
                strike = self._read_number(row, ["strikePrice", "strike", "strike_price"])
                iv = self._read_number(row, ["impliedVolatility", "iv", "IV"])
                if strike <= 0 or iv <= 0 or abs(strike - atm) > step * 5:
                    continue
                nearby.append(iv)
                if abs(strike - atm) < (step / 2):
                    atm_ivs.append(iv)
            if not nearby or not atm_ivs:
                raise RuntimeError("Angel Greeks returned no usable ATM IV")

            atm_iv = sum(atm_ivs) / len(atm_ivs)
            percentile = round(100.0 * sum(1 for iv in nearby if iv <= atm_iv) / len(nearby), 2)
            self._cache_set(cache_key, percentile)
            return percentile
        except Exception as exc:
            # Use exchange IV when present, otherwise solve IV from real option LTP.
            chain = self.get_option_chain(index, spot_price)
            ivs = []
            atm_ivs = []
            for row in chain:
                ce_iv = self._read_number(row, ["call_iv", "ce_iv", "CE_IV", "impliedVolatility"])
                pe_iv = self._read_number(row, ["put_iv", "pe_iv", "PE_IV"])
                strike = self._read_number(row, ["strike", "strikePrice"])
                expiry = str(row.get("expiry", "")).strip()
                expiry_date = None
                if expiry:
                    try:
                        import token_manager
                        expiry_date = token_manager._parse_expiry(expiry)
                    except Exception:
                        expiry_date = None
                if expiry_date and strike > 0:
                    expiry_dt = datetime.combine(expiry_date, datetime.strptime("15:30", "%H:%M").time())
                    years = max((expiry_dt - datetime.now()).total_seconds(), 3600.0) / (365.0 * 24.0 * 3600.0)
                    rate = float(os.getenv("RISK_FREE_RATE", "0.06"))
                    if ce_iv <= 0:
                        ce_ltp = self._read_number(row, ["call_ltp", "ce_ltp", "CE_LTP"])
                        ce_iv = self._solve_implied_volatility(float(spot_price), strike, years, rate, ce_ltp, "CE")
                    if pe_iv <= 0:
                        pe_ltp = self._read_number(row, ["put_ltp", "pe_ltp", "PE_LTP"])
                        pe_iv = self._solve_implied_volatility(float(spot_price), strike, years, rate, pe_ltp, "PE")
                if ce_iv > 0:
                    ivs.append(ce_iv)
                    if abs(strike - atm) < (step / 2):
                        atm_ivs.append(ce_iv)
                if pe_iv > 0:
                    ivs.append(pe_iv)
                    if abs(strike - atm) < (step / 2):
                        atm_ivs.append(pe_iv)
            if not ivs:
                raise RuntimeError(f"Option IV unavailable: {exc}")
            current_iv = sum(atm_ivs or ivs) / len(atm_ivs or ivs)
            percentile = round(100.0 * sum(1 for iv in ivs if iv <= current_iv) / len(ivs), 2)
            self._cache_set(cache_key, percentile)
            return percentile

    def _solve_implied_volatility(self, spot, strike, years, rate, premium, option_type):
        if spot <= 0 or strike <= 0 or years <= 0 or premium <= 0:
            return 0.0
        discount = math.exp(-rate * years)
        intrinsic = max(0.0, spot - strike * discount) if option_type == "CE" else max(0.0, strike * discount - spot)
        if premium <= intrinsic:
            return 0.0

        def normal_cdf(value):
            return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

        def model_price(volatility):
            root_t = math.sqrt(years)
            d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * years) / (volatility * root_t)
            d2 = d1 - volatility * root_t
            if option_type == "CE":
                return spot * normal_cdf(d1) - strike * discount * normal_cdf(d2)
            return strike * discount * normal_cdf(-d2) - spot * normal_cdf(-d1)

        low, high = 0.005, 5.0
        if model_price(high) < premium:
            return 0.0
        for _ in range(70):
            mid = (low + high) / 2.0
            if model_price(mid) > premium:
                high = mid
            else:
                low = mid
        return round(((low + high) / 2.0) * 100.0, 4)

    def get_live_coi_pack(self, index, spot_price):
        chain = self.get_option_chain(index, spot_price)
        if not chain:
            raise RuntimeError("Option chain unavailable")
        call_coi = sum(self._read_number(x, ["call_coi", "ce_coi", "CE_CHNG_OI", "changeinOpenInterest", "callChangeOI"]) for x in chain)
        put_coi = sum(self._read_number(x, ["put_coi", "pe_coi", "PE_CHNG_OI", "changeinOpenInterest", "putChangeOI"]) for x in chain)
        # COI can be zero during market-close or first snapshot.
        # Keep it available as neutral instead of blocking the factor.
        ratio = round((put_coi / call_coi), 2) if call_coi else (1.0 if put_coi == 0 else 9.99)
        if put_coi > call_coi * 1.15:
            signal = "CALL_COI_BULLISH"
        elif call_coi > put_coi * 1.15:
            signal = "PUT_COI_BEARISH"
        else:
            signal = "COI_NEUTRAL"
        return {"call_coi": call_coi, "put_coi": put_coi, "coi_imbalance": ratio, "coi_signal": signal}

    def get_india_vix(self):
        token = os.getenv("INDIA_VIX_TOKEN", "").strip()
        if not token:
            return None
        res = self.fetch_ltp_with_retry("NSE", "INDIA VIX", token, retries=3, delay=0.25)
        if res.get("success"):
            return float(res["data"]["ltp"])
        return None

    def get_top_weighted_confirmation(self):
        symbols = [x.strip() for x in os.getenv("TOP_NIFTY_SYMBOLS", "").split(",") if x.strip()]
        tokens = [x.strip() for x in os.getenv("TOP_NIFTY_TOKENS", "").split(",") if x.strip()]
        raw_weights = [x.strip() for x in os.getenv("TOP_NIFTY_WEIGHTS", "").split(",") if x.strip()]
        if not symbols or len(symbols) != len(tokens):
            return None
        weights = []
        for idx in range(len(symbols)):
            try:
                weights.append(float(raw_weights[idx]))
            except Exception:
                weights.append(1.0)
        cache_key = "angel:top_weighted_confirmation"
        cached = self._cache_get(cache_key, ttl=15)
        if cached is not None:
            return cached

        score = 0.0
        used = 0
        try:
            response = self.broker.getMarketData("FULL", {"NSE": tokens})
            fetched = response.get("data", {}).get("fetched", []) if response and response.get("status") else []
            token_weights = {str(token): weight for token, weight in zip(tokens, weights)}
            for row in fetched:
                token = str(row.get("symbolToken", row.get("symboltoken", "")))
                if token not in token_weights:
                    continue
                change = self._read_number(row, ["netChange", "change", "percentChange"])
                if change == 0:
                    ltp = self._read_number(row, ["ltp", "lastTradedPrice"])
                    close = self._read_number(row, ["close", "previousClose"])
                    change = ltp - close if ltp > 0 and close > 0 else 0.0
                weight = token_weights[token]
                score += weight if change > 0 else -weight if change < 0 else 0.0
                used += 1
        except Exception as exc:
            logger.error(f"Top weighted FULL quote failed: {exc}")

        if used < max(3, len(symbols) // 2):
            return None
        result = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
        self._cache_set(cache_key, result)
        return result

    def get_option_chain_noise(self, index, spot_price):
        try:
            imbalance = self.get_live_chain_imbalance(index, spot_price)
            if imbalance >= 1.2:
                return "PUT_NOISE"
            if imbalance <= 0.8:
                return "CALL_NOISE"
            return "NORMAL"
        except Exception as exc:
            logger.error(f"Option chain noise unavailable: {exc}")
            raise

    def get_live_market_factors(self, index, spot_price):
        """
        Real-only factor pack.
        If option-chain/IV is unavailable, returns availability flags instead of
        invented data. Strategy can continue with zero credit for missing factors.
        """
        factors = {
            "chain_available": False,
            "iv_available": False,
            "coi_available": False,
            "coi_imbalance_available": False,
            "total_oi_available": False,
            "india_vix_available": False,
            "gift_nifty_available": False,
            "ad_ratio_available": False,
            "top_weighted_available": False,
            "chain_imbalance": 1.0,
            "iv_percentile": 50.0,
            "call_coi": 0.0,
            "put_coi": 0.0,
            "coi_imbalance": 1.0,
            "coi_signal": "COI_NEUTRAL",
            "total_oi_volume": 0.0,
            "india_vix": 0.0,
            "gift_nifty": "NEUTRAL",
            "ad_ratio": 1.0,
            "top_weighted_confirmation": "NEUTRAL",
            "oi_chain_noise": "NORMAL",
            "missing": [],
        }
        try:
            factors["chain_imbalance"] = self.get_live_chain_imbalance(index, spot_price)
            factors["oi_chain_noise"] = self.get_option_chain_noise(index, spot_price)
            chain = self.get_option_chain(index, spot_price)
            factors["total_oi_volume"] = sum(
                self._read_number(x, ["call_oi", "ce_oi", "CE_OI", "callOI"])
                + self._read_number(x, ["put_oi", "pe_oi", "PE_OI", "putOI"])
                for x in chain
            )
            factors["total_oi_available"] = factors["total_oi_volume"] > 0
            factors["chain_available"] = True
        except Exception as exc:
            factors["missing"].append(f"chain:{exc}")

        try:
            factors["iv_percentile"] = self.get_live_iv_percentile(index, spot_price)
            factors["iv_available"] = True
        except Exception as exc:
            factors["missing"].append(f"iv:{exc}")

        try:
            factors.update(self.get_live_coi_pack(index, spot_price))
            factors["coi_available"] = True
            factors["coi_imbalance_available"] = True
        except Exception as exc:
            factors["missing"].append(f"coi:{exc}")

        india_vix = self.get_india_vix()
        if india_vix:
            factors["india_vix"] = india_vix
            factors["india_vix_available"] = True
        else:
            factors["missing"].append("india_vix:not_configured")

        gift = self.get_gift_nifty_sentiment()
        if gift:
            factors["gift_nifty"] = gift
            factors["gift_nifty_available"] = True
        else:
            factors["missing"].append("gift_nifty:not_configured")

        ad_ratio = self.get_ad_ratio()
        if ad_ratio is not None:
            factors["ad_ratio"] = ad_ratio
            factors["ad_ratio_available"] = True
        else:
            factors["missing"].append("ad_ratio:not_configured")

        top_confirm = self.get_top_weighted_confirmation()
        if top_confirm:
            factors["top_weighted_confirmation"] = top_confirm
            factors["top_weighted_available"] = True
        else:
            factors["missing"].append("top_weighted:not_configured")

        return factors


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

    if not getattr(token_manager, "_MASTER_MAP", {}):
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

    def depth_quantity(item, side):
        depth_data = item.get("depth", {}) if isinstance(item.get("depth"), dict) else {}
        rows = depth_data.get(side, [])
        if not rows:
            rows = item.get("best_5_buy_data" if side == "buy" else "best_5_sell_data", [])
        return sum(float(level.get("quantity", level.get("qty", 0)) or 0) for level in rows if isinstance(level, dict))

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
        row["expiry"] = expiry
        if opt_type == "CE":
            row["call_oi"] = oi
            row["call_coi"] = coi
            row["call_ltp"] = float(item.get("ltp", item.get("lastTradedPrice", 0)) or 0)
            row["call_volume"] = float(item.get("tradeVolume", 0) or 0)
            row["call_buy_depth_qty"] = depth_quantity(item, "buy")
            row["call_sell_depth_qty"] = depth_quantity(item, "sell")
        else:
            row["put_oi"] = oi
            row["put_coi"] = coi
            row["put_ltp"] = float(item.get("ltp", item.get("lastTradedPrice", 0)) or 0)
            row["put_volume"] = float(item.get("tradeVolume", 0) or 0)
            row["put_buy_depth_qty"] = depth_quantity(item, "buy")
            row["put_sell_depth_qty"] = depth_quantity(item, "sell")

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
        row.setdefault("call_ltp", 0.0)
        row.setdefault("put_ltp", 0.0)
        row.setdefault("call_buy_depth_qty", 0.0)
        row.setdefault("call_sell_depth_qty", 0.0)
        row.setdefault("put_buy_depth_qty", 0.0)
        row.setdefault("put_sell_depth_qty", 0.0)
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


# ===== INSTITUTIONAL LIVE FACTORS: GREEKS / DEALER / MAX PAIN / TOP-15 =====
def _normal_pdf_ms(value):
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def _normal_cdf_ms(value):
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _bs_greeks_ms(spot, strike, years, rate, volatility, option_type):
    if spot <= 0 or strike <= 0 or years <= 0 or volatility <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility * volatility) * years) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    gamma = _normal_pdf_ms(d1) / (spot * volatility * root_t)
    vega = spot * _normal_pdf_ms(d1) * root_t / 100.0
    if option_type == "CE":
        delta = _normal_cdf_ms(d1)
        theta = (
            -spot * _normal_pdf_ms(d1) * volatility / (2.0 * root_t)
            - rate * strike * math.exp(-rate * years) * _normal_cdf_ms(d2)
        ) / 365.0
    else:
        delta = _normal_cdf_ms(d1) - 1.0
        theta = (
            -spot * _normal_pdf_ms(d1) * volatility / (2.0 * root_t)
            + rate * strike * math.exp(-rate * years) * _normal_cdf_ms(-d2)
        ) / 365.0
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }


def _option_years_to_expiry_ms(expiry_text):
    try:
        import token_manager

        expiry_date = token_manager._parse_expiry(expiry_text)
        if not expiry_date:
            expiry_date = token_manager._parse_expiry(str(expiry_text).replace(" ", ""))
        if not expiry_date:
            return 0.0
        expiry_dt = datetime.combine(expiry_date, datetime.strptime("15:30", "%H:%M").time())
        return max((expiry_dt - datetime.now()).total_seconds(), 3600.0) / (365.0 * 24.0 * 3600.0)
    except Exception:
        return 0.0


def _get_real_greeks_pack_ms(self, index, spot_price):
    index = str(index).upper()
    step = 50 if index == "NIFTY" else 100
    atm = int(round(float(spot_price) / step) * step)
    cache_key = f"institutional:greeks:{index}:{atm}"
    cached = self._cache_get(cache_key, ttl=20)
    if cached is not None:
        return cached

    pack = {
        "greeks_available": False,
        "call_delta": 0.0,
        "put_delta": 0.0,
        "call_gamma": 0.0,
        "put_gamma": 0.0,
        "call_theta": 0.0,
        "put_theta": 0.0,
        "call_vega": 0.0,
        "put_vega": 0.0,
        "net_delta": 0.0,
        "net_gamma": 0.0,
        "net_theta": 0.0,
        "net_vega": 0.0,
        "greeks_bias": "NEUTRAL",
        "greeks_source": "NONE",
    }

    try:
        import token_manager

        today = datetime.now().date()
        expiries = []
        for row in getattr(token_manager, "_OPTION_ROWS", []):
            if str(row.get("name", "")).upper() != index:
                continue
            expiry = token_manager._parse_expiry(row.get("expiry"))
            if expiry and expiry >= today:
                expiries.append(expiry)
        if expiries and hasattr(self.broker, "optionGreek"):
            expiry_text = min(expiries).strftime("%d%b%Y").upper()
            response = self.broker.optionGreek({"name": index, "expirydate": expiry_text})
            if response and response.get("status") and isinstance(response.get("data"), list):
                ce_rows, pe_rows = [], []
                for row in response["data"]:
                    strike = self._read_number(row, ["strikePrice", "strike", "strike_price"])
                    if abs(strike - atm) > step:
                        continue
                    opt_type = str(row.get("optionType", row.get("option_type", row.get("instrumenttype", "")))).upper()
                    delta = self._read_number(row, ["delta"])
                    gamma = self._read_number(row, ["gamma"])
                    theta = self._read_number(row, ["theta"])
                    vega = self._read_number(row, ["vega"])
                    target = pe_rows if "PE" in opt_type or delta < 0 else ce_rows
                    target.append((abs(strike - atm), delta, gamma, theta, vega))
                if ce_rows or pe_rows:
                    ce_rows.sort(key=lambda x: x[0])
                    pe_rows.sort(key=lambda x: x[0])
                    ce = ce_rows[0] if ce_rows else (0, 0, 0, 0, 0)
                    pe = pe_rows[0] if pe_rows else (0, 0, 0, 0, 0)
                    pack.update({
                        "greeks_available": True,
                        "call_delta": round(ce[1], 4),
                        "put_delta": round(pe[1], 4),
                        "call_gamma": round(ce[2], 6),
                        "put_gamma": round(pe[2], 6),
                        "call_theta": round(ce[3], 4),
                        "put_theta": round(pe[3], 4),
                        "call_vega": round(ce[4], 4),
                        "put_vega": round(pe[4], 4),
                        "greeks_source": "ANGEL_OPTION_GREEK",
                    })
    except Exception as exc:
        logger.warning(f"Angel Greeks primary unavailable: {exc}")

    if not pack["greeks_available"]:
        chain = self.get_option_chain(index, spot_price, depth=2)
        atm_rows = sorted(chain, key=lambda row: abs(self._read_number(row, ["strike", "strikePrice"]) - atm))
        if not atm_rows:
            raise RuntimeError("Real Greeks unavailable: option chain blank")
        row = atm_rows[0]
        strike = self._read_number(row, ["strike", "strikePrice"])
        years = _option_years_to_expiry_ms(row.get("expiry", ""))
        rate = float(os.getenv("RISK_FREE_RATE", "0.06"))
        ce_iv = self._read_number(row, ["call_iv", "ce_iv", "CE_IV"])
        pe_iv = self._read_number(row, ["put_iv", "pe_iv", "PE_IV"])
        if ce_iv <= 0:
            ce_iv = self._solve_implied_volatility(float(spot_price), strike, years, rate, self._read_number(row, ["call_ltp", "ce_ltp"]), "CE")
        if pe_iv <= 0:
            pe_iv = self._solve_implied_volatility(float(spot_price), strike, years, rate, self._read_number(row, ["put_ltp", "pe_ltp"]), "PE")
        ce_g = _bs_greeks_ms(float(spot_price), strike, years, rate, ce_iv / 100.0, "CE")
        pe_g = _bs_greeks_ms(float(spot_price), strike, years, rate, pe_iv / 100.0, "PE")
        if any(abs(ce_g[k]) > 0 for k in ce_g) or any(abs(pe_g[k]) > 0 for k in pe_g):
            pack.update({
                "greeks_available": True,
                "call_delta": ce_g["delta"],
                "put_delta": pe_g["delta"],
                "call_gamma": ce_g["gamma"],
                "put_gamma": pe_g["gamma"],
                "call_theta": ce_g["theta"],
                "put_theta": pe_g["theta"],
                "call_vega": ce_g["vega"],
                "put_vega": pe_g["vega"],
                "greeks_source": "REAL_LTP_BLACK_SCHOLES",
            })

    if pack["greeks_available"]:
        pack["net_delta"] = round(pack["call_delta"] + pack["put_delta"], 4)
        pack["net_gamma"] = round(pack["call_gamma"] - pack["put_gamma"], 6)
        pack["net_theta"] = round(pack["call_theta"] + pack["put_theta"], 4)
        pack["net_vega"] = round(pack["call_vega"] - pack["put_vega"], 4)
        if pack["net_delta"] >= 0.08:
            pack["greeks_bias"] = "BULLISH"
        elif pack["net_delta"] <= -0.08:
            pack["greeks_bias"] = "BEARISH"
        else:
            pack["greeks_bias"] = "NEUTRAL"
        self._cache_set(cache_key, pack)
    return pack


def _get_max_pain_pack_ms(self, index, spot_price):
    chain = self.get_option_chain(index, spot_price, depth=8)
    rows = []
    for row in chain:
        strike = self._read_number(row, ["strike", "strikePrice"])
        call_oi = self._read_number(row, ["call_oi", "ce_oi", "CE_OI", "callOI"])
        put_oi = self._read_number(row, ["put_oi", "pe_oi", "PE_OI", "putOI"])
        if strike > 0 and (call_oi > 0 or put_oi > 0):
            rows.append((strike, call_oi, put_oi))
    if not rows:
        raise RuntimeError("Max Pain unavailable: OI chain blank")
    strikes = [x[0] for x in rows]
    pain_by_strike = {}
    for test_strike in strikes:
        total_pain = 0.0
        for strike, call_oi, put_oi in rows:
            total_pain += max(0.0, test_strike - strike) * call_oi
            total_pain += max(0.0, strike - test_strike) * put_oi
        pain_by_strike[test_strike] = total_pain
    max_pain = min(pain_by_strike, key=pain_by_strike.get)
    distance = round(float(spot_price) - max_pain, 2)
    bias = "BULLISH_MAGNET" if distance < -50 else "BEARISH_MAGNET" if distance > 50 else "PINNED"
    return {
        "max_pain_available": True,
        "max_pain": float(max_pain),
        "max_pain_distance": distance,
        "max_pain_bias": bias,
    }


def _get_dealer_position_pack_ms(self, index, spot_price):
    greeks = self.get_real_greeks_pack(index, spot_price)
    if not greeks.get("greeks_available"):
        raise RuntimeError("Dealer position unavailable: Greeks unavailable")
    chain = self.get_option_chain(index, spot_price, depth=4)
    customer_delta = customer_gamma = customer_vega = 0.0
    for row in chain:
        call_oi = self._read_number(row, ["call_oi", "ce_oi", "CE_OI", "callOI"])
        put_oi = self._read_number(row, ["put_oi", "pe_oi", "PE_OI", "putOI"])
        customer_delta += call_oi * greeks["call_delta"] + put_oi * greeks["put_delta"]
        customer_gamma += call_oi * greeks["call_gamma"] + put_oi * greeks["put_gamma"]
        customer_vega += call_oi * greeks["call_vega"] + put_oi * greeks["put_vega"]
    dealer_delta = -customer_delta
    dealer_gamma = -customer_gamma
    dealer_vega = -customer_vega
    pressure = dealer_delta / max(abs(customer_delta), 1.0)
    if pressure > 0.15:
        signal = "DEALER_SUPPORT"
    elif pressure < -0.15:
        signal = "DEALER_RESISTANCE"
    else:
        signal = "DEALER_NEUTRAL"
    return {
        "dealer_position_available": True,
        "dealer_delta": round(dealer_delta, 2),
        "dealer_gamma": round(dealer_gamma, 4),
        "dealer_vega": round(dealer_vega, 2),
        "dealer_hedge_pressure": round(pressure, 4),
        "dealer_position_signal": signal,
    }


def _get_order_book_imbalance_pack_ms(self, index, spot_price):
    chain = self.get_option_chain(index, spot_price, depth=8)
    bullish_pressure = sum(
        self._read_number(row, ["call_buy_depth_qty"]) + self._read_number(row, ["put_sell_depth_qty"])
        for row in chain
    )
    bearish_pressure = sum(
        self._read_number(row, ["call_sell_depth_qty"]) + self._read_number(row, ["put_buy_depth_qty"])
        for row in chain
    )
    total = bullish_pressure + bearish_pressure
    if total <= 0:
        raise RuntimeError("Angel best-five option depth unavailable")
    ratio = bullish_pressure / max(bearish_pressure, 1.0)
    direction = "BULLISH" if ratio >= 1.15 else "BEARISH" if ratio <= 0.87 else "NEUTRAL"
    return {
        "order_book_imbalance_available": True,
        "order_book_imbalance": round(ratio, 4),
        "order_book_direction": direction,
        "order_book_bullish_qty": round(bullish_pressure, 2),
        "order_book_bearish_qty": round(bearish_pressure, 2),
        "order_book_source": "ANGEL_FULL_BEST5",
    }


def _resolve_top15_universe_ms(self):
    symbols = [x.strip().upper() for x in os.getenv("TOP_NIFTY_SYMBOLS", "").split(",") if x.strip()]
    tokens = [x.strip() for x in os.getenv("TOP_NIFTY_TOKENS", "").split(",") if x.strip()]
    weights = [x.strip() for x in os.getenv("TOP_NIFTY_WEIGHTS", "").split(",") if x.strip()]
    if symbols and len(symbols) == len(tokens):
        return symbols[:15], tokens[:15], [float(weights[i]) if i < len(weights) else 1.0 for i in range(min(15, len(symbols)))]

    default_symbols = [
        "HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "BHARTIARTL",
        "LT", "ITC", "TCS", "AXISBANK", "SBIN",
        "KOTAKBANK", "M&M", "BAJFINANCE", "HINDUNILVR", "MARUTI",
    ]
    default_weights = [13.0, 9.0, 8.0, 5.0, 4.5, 4.0, 3.8, 3.6, 3.2, 3.0, 2.8, 2.5, 2.3, 2.2, 2.0]
    try:
        import json

        with open("token_master.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("data", []) if isinstance(data, dict) else data
        by_name = {}
        for row in rows:
            if row.get("exch_seg") != "NSE":
                continue
            symbol = str(row.get("symbol", "")).upper()
            name = str(row.get("name", "")).upper()
            token = str(row.get("token", "")).strip()
            if not token:
                continue
            for base in default_symbols:
                if symbol in (base, f"{base}-EQ") or name == base:
                    by_name.setdefault(base, token)
        final_symbols, final_tokens, final_weights = [], [], []
        for symbol, weight in zip(default_symbols, default_weights):
            token = by_name.get(symbol)
            if token:
                final_symbols.append(symbol)
                final_tokens.append(token)
                final_weights.append(weight)
        return final_symbols, final_tokens, final_weights
    except Exception as exc:
        logger.error(f"Top-15 universe auto-resolve failed: {exc}")
        return [], [], []


def _get_top15_weighted_confirmation_ms(self):
    cache_key = "angel:top15_weighted_confirmation_refined"
    cached = self._cache_get(cache_key, ttl=15)
    if cached is not None:
        return cached
    symbols, tokens, weights = self._resolve_top15_universe()
    if len(tokens) < 10:
        raise RuntimeError(f"Top-15 breadth unavailable: only {len(tokens)} tokens resolved")
    response = self.broker.getMarketData("FULL", {"NSE": tokens})
    fetched = response.get("data", {}).get("fetched", []) if response and response.get("status") else []
    if len(fetched) < 10:
        raise RuntimeError(f"Top-15 breadth unavailable: only {len(fetched)} quotes fetched")
    weight_by_token = {str(token): weight for token, weight in zip(tokens, weights)}
    symbol_by_token = {str(token): symbol for symbol, token in zip(symbols, tokens)}
    score = 0.0
    bullish = bearish = used = 0
    rows = []
    for item in fetched:
        token = str(item.get("symbolToken", item.get("symboltoken", "")))
        if token not in weight_by_token:
            continue
        change_pct = self._read_number(item, ["percentChange", "pChange"])
        change = self._read_number(item, ["netChange", "change"])
        if change_pct == 0 and change == 0:
            ltp = self._read_number(item, ["ltp", "lastTradedPrice"])
            close = self._read_number(item, ["close", "previousClose"])
            change = ltp - close if ltp > 0 and close > 0 else 0.0
        direction = 1 if (change_pct > 0 or change > 0) else -1 if (change_pct < 0 or change < 0) else 0
        weight = weight_by_token[token]
        score += weight * direction
        bullish += int(direction > 0)
        bearish += int(direction < 0)
        used += 1
        rows.append({"symbol": symbol_by_token.get(token, token), "weight": weight, "direction": direction})
    total_weight = sum(weight_by_token.values()) or 1.0
    strength = round((score / total_weight) * 100.0, 2)
    direction = "BULLISH" if strength >= 8 else "BEARISH" if strength <= -8 else "NEUTRAL"
    result = {
        "top15_weighted_available": used >= 10,
        "top15_weighted_confirmation": direction,
        "top15_weighted_score": strength,
        "top15_bullish_count": bullish,
        "top15_bearish_count": bearish,
        "top15_used_count": used,
        "top15_rows": rows,
    }
    self._cache_set(cache_key, result)
    return result


DataFetcher.get_real_greeks_pack = _get_real_greeks_pack_ms
DataFetcher.get_max_pain_pack = _get_max_pain_pack_ms
DataFetcher.get_dealer_position_pack = _get_dealer_position_pack_ms
DataFetcher.get_order_book_imbalance_pack = _get_order_book_imbalance_pack_ms
DataFetcher._resolve_top15_universe = _resolve_top15_universe_ms
DataFetcher.get_top15_weighted_confirmation = _get_top15_weighted_confirmation_ms

_old_live_market_factors_ms = DataFetcher.get_live_market_factors


def _get_live_market_factors_institutional_ms(self, index, spot_price):
    factors = _old_live_market_factors_ms(self, index, spot_price)
    factors.update({
        "greeks_available": False,
        "dealer_position_available": False,
        "max_pain_available": False,
        "top15_weighted_available": False,
        "order_book_imbalance_available": False,
        "greeks_bias": "NEUTRAL",
        "dealer_position_signal": "DEALER_NEUTRAL",
        "max_pain_bias": "PINNED",
        "top15_weighted_confirmation": "NEUTRAL",
        "order_book_direction": "NEUTRAL",
    })
    try:
        factors.update(self.get_real_greeks_pack(index, spot_price))
    except Exception as exc:
        factors["missing"].append(f"greeks:{exc}")
    try:
        factors.update(self.get_dealer_position_pack(index, spot_price))
    except Exception as exc:
        factors["missing"].append(f"dealer_position:{exc}")
    try:
        factors.update(self.get_max_pain_pack(index, spot_price))
    except Exception as exc:
        factors["missing"].append(f"max_pain:{exc}")
    try:
        factors.update(self.get_order_book_imbalance_pack(index, spot_price))
    except Exception as exc:
        factors["missing"].append(f"order_book:{exc}")
    try:
        top15 = self.get_top15_weighted_confirmation()
        factors.update(top15)
        if top15.get("top15_weighted_available"):
            factors["top_weighted_confirmation"] = top15.get("top15_weighted_confirmation", factors.get("top_weighted_confirmation", "NEUTRAL"))
            factors["top_weighted_available"] = True
    except Exception as exc:
        factors["missing"].append(f"top15_weighted:{exc}")
    return factors


DataFetcher.get_live_market_factors = _get_live_market_factors_institutional_ms
# ===== END INSTITUTIONAL LIVE FACTORS =====

