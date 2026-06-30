import os
import json
import threading
import time
from logger import logger
from execution_monitor import ExecutionQualityMonitor
from reward_engine import trailing_stop_logic


class TradeManager:
    def __init__(self, broker, session_refresher=None):
        self.broker = broker
        self.session_refresher = session_refresher
        self.active_trades = []
        self.last_exit_time = 0
        self.current_capital = self._read_capital()
        self.daily_loss_limit = -abs(float(os.getenv("DAILY_LOSS_LIMIT", "4000")))
        self.today_pnl = 0.0
        self.current_consecutive_losses = 0
        self.current_date_tracker = time.strftime("%Y-%m-%d")
        self.peak_capital = self.current_capital
        self.LOT_SIZE_NIFTY = int(os.getenv("LOT_SIZE_NIFTY", 65))
        self.LOT_SIZE_BANKNIFTY = int(os.getenv("LOT_SIZE_BANKNIFTY", 30))
        self.MAX_EXCHANGE_LOTS = int(os.getenv("MAX_EXCHANGE_LOTS", 27))
        self.max_trades_per_day = int(os.getenv("MAX_TRADE_PER_DAY", 10))
        self.broker_sl_enabled = str(os.getenv("BROKER_SL_ORDER", "TRUE")).strip().upper() == "TRUE"
        self.sl_step = float(os.getenv("BROKER_SL_STEP", "0.5"))
        self.total_trades_today = 0
        self.total_ce_pnl = 0.0
        self.total_pe_pnl = 0.0
        self._lock = threading.Lock()
        self._state_file = os.path.join("logs", "open_trades_state.json")
        self._daily_risk_file = os.path.join("logs", "daily_risk_state.json")
        self.daily_peak_pnl = 0.0
        self.daily_profit_floor = 0.0
        self.daily_halt = False
        self.daily_halt_reason = ""
        self.last_closed_trade = {}
        self.reentries_today = 0
        self.execution_monitor = ExecutionQualityMonitor()
        self.last_sizing_decision = {}
        if str(os.getenv("RECOVER_DAILY_RISK", "TRUE")).strip().upper() == "TRUE":
            self._load_daily_risk_state()
        if str(os.getenv("RECOVER_OPEN_TRADES", "TRUE")).strip().upper() == "TRUE":
            self._load_state()

    def set_broker(self, broker):
        self.broker = broker

    def _save_state(self):
        try:
            os.makedirs("logs", exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as fh:
                json.dump(self.active_trades, fh, indent=2)
        except Exception as exc:
            logger.error(f"Open trade state save failed: {exc}")

    def _save_daily_risk_state(self):
        try:
            os.makedirs("logs", exist_ok=True)
            payload = {
                "date": self.current_date_tracker,
                "peak_pnl": self.daily_peak_pnl,
                "profit_floor": self.daily_profit_floor,
                "halt": self.daily_halt,
                "halt_reason": self.daily_halt_reason,
                "trades": self.total_trades_today,
                "consecutive_losses": self.current_consecutive_losses,
                "last_closed_trade": self.last_closed_trade,
                "reentries_today": self.reentries_today,
            }
            with open(self._daily_risk_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except Exception as exc:
            logger.error(f"Daily risk state save failed: {exc}")

    def _load_daily_risk_state(self):
        try:
            if not os.path.exists(self._daily_risk_file):
                return
            with open(self._daily_risk_file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if payload.get("date") != self.current_date_tracker:
                return
            self.daily_peak_pnl = float(payload.get("peak_pnl", 0.0))
            self.daily_profit_floor = float(payload.get("profit_floor", 0.0))
            self.daily_halt = bool(payload.get("halt", False))
            self.daily_halt_reason = str(payload.get("halt_reason", ""))
            self.total_trades_today = max(self.total_trades_today, int(payload.get("trades", 0)))
            self.current_consecutive_losses = max(self.current_consecutive_losses, int(payload.get("consecutive_losses", 0)))
            self.last_closed_trade = payload.get("last_closed_trade", {}) if isinstance(payload.get("last_closed_trade", {}), dict) else {}
            self.last_exit_time = max(self.last_exit_time, float(self.last_closed_trade.get("closed_epoch", 0.0) or 0.0))
            self.reentries_today = int(payload.get("reentries_today", 0))
        except Exception as exc:
            logger.error(f"Daily risk state load failed: {exc}")

    def _load_state(self):
        try:
            if not os.path.exists(self._state_file):
                return
            with open(self._state_file, "r", encoding="utf-8") as fh:
                rows = json.load(fh)
            if not isinstance(rows, list):
                return
            recovered = []
            for trade in rows:
                if not isinstance(trade, dict) or not trade.get("symbol") or not trade.get("token"):
                    continue
                live_qty = self._position_qty_for_symbol(trade["symbol"])
                if live_qty is None or live_qty != 0:
                    if live_qty and live_qty > 0:
                        trade["qty"] = live_qty
                    recovered.append(trade)
            self.active_trades = recovered
            if recovered:
                logger.warning(f"Recovered {len(recovered)} open trade(s) from VPS state file.")
        except Exception as exc:
            logger.error(f"Open trade state recovery failed: {exc}")

    def _read_capital(self):
        try:
            res = self.broker.rmsLimit()
            if res and res.get("status") and res.get("data"):
                data = res["data"]
                return float(data.get("net", data.get("availablecash", os.getenv("INITIAL_CAPITAL", 25000))))
        except Exception as exc:
            logger.error(f"RMS read failed: {exc}")
        return float(os.getenv("INITIAL_CAPITAL", 25000))

    def refresh_day_if_needed(self):
        today = time.strftime("%Y-%m-%d")
        if self.current_date_tracker != today:
            self.today_pnl = 0.0
            self.total_trades_today = 0
            self.total_ce_pnl = 0.0
            self.total_pe_pnl = 0.0
            self.current_consecutive_losses = 0
            self.daily_peak_pnl = 0.0
            self.daily_profit_floor = 0.0
            self.daily_halt = False
            self.daily_halt_reason = ""
            self.last_closed_trade = {}
            self.reentries_today = 0
            self.current_date_tracker = today
            self._save_daily_risk_state()

    def _profit_floor_for_peak(self, peak_pnl):
        peak_pnl = float(peak_pnl)
        start = float(os.getenv("DAILY_PROFIT_LOCK_START", "2000"))
        full_lock = float(os.getenv("DAILY_PROFIT_LOCK_FULL_AT", "5000"))
        giveback = float(os.getenv("DAILY_PROFIT_GIVEBACK", "1250"))
        if peak_pnl < start:
            return 0.0
        if peak_pnl < full_lock:
            return max(0.0, peak_pnl * float(os.getenv("DAILY_LOW_PROFIT_LOCK_RATIO", "0.60")))
        return max(0.0, peak_pnl - giveback)

    def _estimate_live_pnl(self):
        live_pnl = 0.0
        for trade in list(self.active_trades):
            try:
                curr_ltp = self.get_premium_ltp(trade["symbol"], trade["token"])
            except Exception:
                curr_ltp = float(trade.get("max_prem", trade.get("avg_entry_prem", 0.0)))
            live_pnl += (curr_ltp - float(trade["avg_entry_prem"])) * int(trade["qty"])
        return live_pnl

    def _read_broker_day_pnl(self):
        try:
            res = self.broker.position()
            if not (res and res.get("status") and isinstance(res.get("data"), list)):
                return None
            values = []
            for position in res["data"]:
                if position.get("pnl") not in (None, ""):
                    values.append(float(position.get("pnl") or 0.0))
            return sum(values) if values else None
        except Exception as exc:
            logger.error(f"Broker day PnL read failed: {exc}")
            return None

    def get_net_day_pnl(self):
        if str(os.getenv("BROKER_PNL_AUTHORITATIVE", "TRUE")).strip().upper() == "TRUE":
            broker_pnl = self._read_broker_day_pnl()
            if broker_pnl is not None:
                return float(broker_pnl)
        return float(self.today_pnl) + self._estimate_live_pnl()

    def can_open_new_trade(self):
        self.refresh_day_if_needed()
        if self.daily_halt:
            return False, f"Daily equity shield halted entries: {self.daily_halt_reason}"
        if self.get_net_day_pnl() <= self.daily_loss_limit:
            return False, "Daily loss limit hit"
        if self.current_consecutive_losses >= int(os.getenv("MAX_CONSECUTIVE_LOSSES", "2")):
            return False, "Consecutive-loss circuit breaker active"
        if time.strftime("%H:%M") >= os.getenv("LAST_NEW_ENTRY_TIME", "14:45"):
            return False, "Fresh-entry cutoff time reached"
        if self.total_trades_today >= self.max_trades_per_day:
            return False, f"Daily trade cap reached ({self.total_trades_today}/{self.max_trades_per_day})"
        return True, ""

    def evaluate_same_direction_reentry(self, option_type, regime, signal, strategy_inputs):
        if str(os.getenv("ALLOW_STRONG_TREND_REENTRY", "TRUE")).strip().upper() != "TRUE":
            return False, "Strong-trend re-entry disabled"
        previous = self.last_closed_trade or {}
        if not previous:
            return False, "No previous trailing exit"
        if self.reentries_today >= int(os.getenv("MAX_STRONG_TREND_REENTRIES", "2")):
            return False, "Strong-trend re-entry cap reached"
        age = time.time() - float(previous.get("closed_epoch", 0.0))
        min_wait = float(os.getenv("STRONG_REENTRY_MIN_SECONDS", "20"))
        max_wait = float(os.getenv("STRONG_REENTRY_MAX_SECONDS", "300"))
        if age < min_wait or age > max_wait:
            return False, "Outside strong-trend re-entry window"
        if previous.get("type") != option_type or not previous.get("profitable_trailing_exit"):
            return False, "Previous exit was not a profitable same-direction trail"
        strong_regimes = ("STRONG_TREND", "HIGH_VOL_OPEN")
        if previous.get("regime") not in strong_regimes or regime not in strong_regimes:
            return False, "Strong trend not preserved"
        expected_side = "BUY" if option_type == "CE" else "SELL"
        if signal.get("side") != expected_side:
            return False, "Fresh consensus does not confirm the previous direction"
        if float(signal.get("match_percent", 0.0)) < float(os.getenv("STRONG_REENTRY_MIN_MATCH_PERCENT", "72")):
            return False, "Re-entry consensus below minimum"
        if int(signal.get("direction_lead", 0)) < int(os.getenv("STRONG_REENTRY_MIN_DIRECTION_LEAD", "5")):
            return False, "Re-entry direction lead below minimum"
        bullish = option_type == "CE"
        momentum_ok = int(strategy_inputs.get("momentum", 0)) > 0 if bullish else int(strategy_inputs.get("momentum", 0)) < 0
        micro_expected = "BULLISH" if bullish else "BEARISH"
        micro_ok = strategy_inputs.get("micro_trend_direction") == micro_expected
        price_action_ok = strategy_inputs.get("price_action") == ("BREAKOUT" if bullish else "BREAKDOWN")
        retest_ok = strategy_inputs.get("vwap_bounce") == "RETEST_ZONE"
        if not momentum_ok or not (micro_ok or price_action_ok or retest_ok):
            return False, "Waiting for pullback recovery confirmation"
        return True, "Qualified profitable-trail same-direction re-entry"

    def enforce_daily_equity_shield(self):
        self.refresh_day_if_needed()
        net_pnl = self.get_net_day_pnl()
        changed = False
        if net_pnl > self.daily_peak_pnl:
            self.daily_peak_pnl = net_pnl
            changed = True
        new_floor = self._profit_floor_for_peak(self.daily_peak_pnl)
        if new_floor > self.daily_profit_floor:
            self.daily_profit_floor = new_floor
            changed = True
        loss_limit_hit = net_pnl <= self.daily_loss_limit
        profit_floor_hit = self.daily_profit_floor > 0 and net_pnl <= self.daily_profit_floor
        if (loss_limit_hit or profit_floor_hit) and not self.daily_halt:
            self.daily_halt = True
            if loss_limit_hit:
                self.daily_halt_reason = f"Net PnL {net_pnl:.2f} reached daily loss limit {self.daily_loss_limit:.2f}"
            else:
                self.daily_halt_reason = f"Net PnL {net_pnl:.2f} reached locked floor {self.daily_profit_floor:.2f}"
            logger.warning(f"DAILY EQUITY SHIELD TRIGGERED: {self.daily_halt_reason}")
            changed = True
            for trade in list(self.active_trades):
                try:
                    exit_ltp = self.get_premium_ltp(trade["symbol"], trade["token"])
                    self.close_individual_trade(trade, exit_ltp, reason="DAILY_EQUITY_SHIELD")
                except Exception as exc:
                    logger.exception(f"Daily shield exit failed for {trade.get('symbol')}: {exc}")
        if changed:
            self._save_daily_risk_state()
        return {"net_pnl": net_pnl, "peak_pnl": self.daily_peak_pnl, "locked_floor": self.daily_profit_floor, "halted": self.daily_halt}

    def _looks_like_auth_error(self, payload):
        text = str(payload).upper()
        return "TOKEN" in text or "AG8003" in text or "SESSION" in text or "AUTH" in text

    def _refresh_session_if_possible(self):
        if self.session_refresher:
            self.broker = self.session_refresher()

    def get_premium_ltp(self, symbol, token, retries=5):
        if not token:
            raise RuntimeError(f"Missing token for {symbol}")
        last = None
        for attempt in range(retries):
            try:
                res = self.broker.ltpData("NFO", str(symbol).strip(), str(token).strip())
                if res and (res.get("status") or res.get("success")) and res.get("data"):
                    val = float(res["data"].get("ltp", 0))
                    if val > 0:
                        return val
                last = res
                if self._looks_like_auth_error(res):
                    self._refresh_session_if_possible()
            except Exception as exc:
                last = exc
                if self._looks_like_auth_error(exc):
                    self._refresh_session_if_possible()
            time.sleep(0.25 * (1 + attempt))
        raise RuntimeError(f"Premium LTP unavailable for {symbol}/{token}: {last}")

    def get_compounded_qty(self, symbol, wallet_capital, premium, regime, score_smc, sl_points, trade_grade="B", lot_size=None, match_percent=0.0, day_risk_multiplier=1.0, regime_stability=0.0):
        is_bn = "BANKNIFTY" in str(symbol).upper()
        lot = int(lot_size or (self.LOT_SIZE_BANKNIFTY if is_bn else self.LOT_SIZE_NIFTY))
        regime_upper = str(regime).upper()
        allocation = 0.25 if regime_upper in ["SIDEWAYS_CHOPPY", "LOW_ATR_DEAD", "CHOPPY", "VOLATILE"] else 0.45
        if regime_upper in ["STRONG_TREND", "HIGH_VOL_OPEN"] and trade_grade == "A+" and int(score_smc) >= 70:
            allocation = 0.55
        confidence_scale = max(0.85, min(1.15, 0.85 + max(0.0, float(match_percent) - 60.0) / 70.0))
        stability_scale = max(0.90, min(1.05, 0.90 + float(regime_stability) * 0.25))
        day_scale = max(0.50, min(1.0, float(day_risk_multiplier)))
        allocation *= confidence_scale * stability_scale * day_scale
        affordable_lots = int((float(wallet_capital) * allocation) // (float(premium) * lot))
        risk_percent = float(os.getenv("MAX_RISK_PER_TRADE_PERCENT", "5.0")) / 100.0
        if regime_upper in ["SIDEWAYS_CHOPPY", "LOW_ATR_DEAD"]:
            risk_percent *= 0.70
        risk_percent *= confidence_scale * stability_scale * day_scale
        risk_budget = float(wallet_capital) * risk_percent
        risk_lots = int(risk_budget // (max(float(sl_points), 0.05) * lot))
        target_lots = min(affordable_lots, risk_lots, self.MAX_EXCHANGE_LOTS)
        if self.daily_profit_floor > 0:
            headroom = max(0.0, self.get_net_day_pnl() - self.daily_profit_floor)
            headroom_lots = int(headroom // (max(float(sl_points), 0.05) * lot))
            target_lots = min(target_lots, headroom_lots)
        if target_lots < 1:
            self.last_sizing_decision = {
                "lot_size": lot,
                "target_lots": 0,
                "risk_budget": round(risk_budget, 2),
                "allocation": round(allocation, 4),
                "confidence_scale": round(confidence_scale, 4),
                "stability_scale": round(stability_scale, 4),
                "day_scale": round(day_scale, 4),
                "blocked": True,
            }
            return 0
        self.last_sizing_decision = {
            "lot_size": lot,
            "target_lots": target_lots,
            "risk_budget": round(risk_budget, 2),
            "allocation": round(allocation, 4),
            "confidence_scale": round(confidence_scale, 4),
            "stability_scale": round(stability_scale, 4),
            "day_scale": round(day_scale, 4),
            "blocked": False,
        }
        return int(target_lots * lot)

    def record_missed_trade(self, reason, signal=None, context=None):
        self.execution_monitor.record_missed(reason, signal, context)

    def get_execution_summary(self):
        return self.execution_monitor.summary()

    def _place_order_blocking(self, params, retries=3):
        last = None
        for attempt in range(retries):
            logger.info(f"Placing order attempt {attempt + 1}: {params}")
            try:
                res = self.broker.placeOrder(params)
                logger.info(f"Broker order response: {res}")
                if isinstance(res, str) and res.strip():
                    return res, res
                if isinstance(res, dict) and (res.get("status") or res.get("success")):
                    order_id = res.get("data", {}).get("orderid") if isinstance(res.get("data"), dict) else res.get("orderid")
                    return order_id or str(res), res
                last = res
                if self._looks_like_auth_error(res):
                    self._refresh_session_if_possible()
            except Exception as exc:
                last = exc
                logger.error(f"Order attempt failed: {exc}")
                if self._looks_like_auth_error(exc):
                    self._refresh_session_if_possible()
            time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"Broker rejected order after retries: {last}")

    def _resolve_order_fill_price(self, order_id, fallback_price, attempts=8):
        if not order_id:
            return float(fallback_price)
        for _ in range(attempts):
            try:
                res = self.broker.orderBook()
                if res and res.get("status") and res.get("data"):
                    for order in res["data"]:
                        oid = str(order.get("orderid", order.get("orderId", "")))
                        if oid != str(order_id):
                            continue
                        avg = order.get("averageprice", order.get("averagePrice", order.get("avgPrice", 0)))
                        status = str(order.get("status", "")).lower()
                        if avg not in (None, "", "0", 0) and float(avg) > 0:
                            return float(avg)
                        if "reject" in status or "cancel" in status:
                            raise RuntimeError(f"Order not filled: {order}")
            except RuntimeError:
                raise
            except Exception as exc:
                logger.error(f"Order fill price lookup failed for {order_id}: {exc}")
            time.sleep(0.4)
        return float(fallback_price)

    def _sl_price_pair(self, sl_value):
        trigger = round(float(sl_value), 1)
        price = round(max(0.05, trigger - float(os.getenv("BROKER_SL_LIMIT_GAP", "1.0"))), 1)
        return price, trigger

    def _place_broker_sl_order(self, trade, sl_value):
        if not self.broker_sl_enabled or str(os.getenv("LIVE_TRADING", "FALSE")).strip().upper() != "TRUE":
            return None, None
        price, trigger = self._sl_price_pair(sl_value)
        params = {
            "variety": "STOPLOSS",
            "tradingsymbol": str(trade["symbol"]).strip(),
            "symboltoken": str(trade["token"]).strip(),
            "transactiontype": "SELL",
            "exchange": "NFO",
            "ordertype": "STOPLOSS_LIMIT",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": str(price),
            "triggerprice": str(trigger),
            "quantity": str(trade["qty"]),
        }
        order_id, raw = self._place_order_blocking(params)
        logger.info(f"Broker-side SL placed for {trade['symbol']} at trigger {trigger}: {order_id}")
        return order_id, raw

    def _modify_broker_sl_order(self, trade, new_sl, force=False):
        if not trade.get("sl_order_id"):
            return
        price, trigger = self._sl_price_pair(new_sl)
        last_trigger = float(trade.get("broker_sl_trigger", 0.0))
        if not force and trigger <= last_trigger + self.sl_step:
            return
        params = {
            "variety": "STOPLOSS",
            "orderid": str(trade["sl_order_id"]),
            "tradingsymbol": str(trade["symbol"]).strip(),
            "symboltoken": str(trade["token"]).strip(),
            "transactiontype": "SELL",
            "exchange": "NFO",
            "ordertype": "STOPLOSS_LIMIT",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": str(price),
            "triggerprice": str(trigger),
            "quantity": str(trade["qty"]),
        }
        res = self.broker.modifyOrder(params)
        if isinstance(res, dict) and not (res.get("status") or res.get("success")):
            raise RuntimeError(f"Broker SL modify rejected: {res}")
        trade["broker_sl_trigger"] = trigger
        trade["broker_sl_price"] = price
        logger.info(f"Broker-side SL trailed for {trade['symbol']} to trigger {trigger}: {res}")
        self._save_state()

    def _cancel_broker_sl_order(self, trade):
        if not trade.get("sl_order_id") or str(os.getenv("LIVE_TRADING", "FALSE")).strip().upper() != "TRUE":
            return "NOT_REQUIRED"
        try:
            response = self.broker.cancelOrder(str(trade["sl_order_id"]), "STOPLOSS")
            if isinstance(response, dict) and not (response.get("status") or response.get("success")):
                raise RuntimeError(f"Broker rejected SL cancellation: {response}")
            logger.info(f"Broker-side SL cancelled for {trade['symbol']}: {trade['sl_order_id']}")
            for _ in range(5):
                try:
                    book = self.broker.orderBook()
                    if book and book.get("status") and book.get("data"):
                        matching = [row for row in book["data"] if str(row.get("orderid", row.get("orderId", ""))) == str(trade["sl_order_id"])]
                        if not matching:
                            break
                        status = str(matching[0].get("status", "")).lower()
                        if "complete" in status or "fill" in status:
                            return "ALREADY_FILLED"
                        if "cancel" in status or "reject" in status:
                            return "CANCELLED"
                except Exception as poll_exc:
                    logger.error(f"SL cancel confirmation lookup failed: {poll_exc}")
                time.sleep(0.2)
            live_qty = self._position_qty_for_symbol(trade["symbol"])
            return "ALREADY_FILLED" if live_qty == 0 else "CANCELLED"
        except Exception as exc:
            logger.error(f"Broker-side SL cancel failed for {trade['symbol']}: {exc}")
            raise

    def _position_qty_for_symbol(self, symbol):
        try:
            res = self.broker.position()
            if res and res.get("status") and res.get("data"):
                for pos in res["data"]:
                    if str(pos.get("tradingsymbol", "")).strip() == str(symbol).strip():
                        return int(float(pos.get("netqty", pos.get("netQty", 0)) or 0))
        except Exception as exc:
            logger.error(f"Position sync failed for {symbol}: {exc}")
        return None

    def get_broker_positions_rows(self):
        rows = []
        try:
            res = self.broker.position()
            if res and res.get("status") and res.get("data"):
                for pos in res["data"]:
                    net_qty = int(float(pos.get("netqty", pos.get("netQty", 0)) or 0))
                    if net_qty == 0:
                        continue
                    rows.append({
                        "symbol": str(pos.get("tradingsymbol", "")),
                        "qty": net_qty,
                        "buy_avg": float(pos.get("buyavgprice", pos.get("buyAvgPrice", 0)) or 0),
                        "sell_avg": float(pos.get("sellavgprice", pos.get("sellAvgPrice", 0)) or 0),
                        "ltp": float(pos.get("ltp", 0) or 0),
                        "pnl": float(pos.get("pnl", 0) or 0),
                        "product": str(pos.get("producttype", pos.get("productType", ""))),
                    })
        except Exception as exc:
            logger.error(f"Broker positions dashboard sync failed: {exc}")
        return rows

    def open_trade(self, trade_data):
        with self._lock:
            allowed, reason = self.can_open_new_trade()
            if not allowed:
                logger.warning(f"Entry blocked: {reason}")
                return False
            if any(str(t["token"]) == str(trade_data["token"]) for t in self.active_trades):
                return False

            entry_ltp = self.get_premium_ltp(trade_data["symbol"], trade_data["token"])
            reference_entry_ltp = entry_ltp
            regime = trade_data.get("regime", "NORMAL")
            score_smc = trade_data.get("smc_score", 50)
            atr_points = max(1.0, float(trade_data.get("atr_points", 12.0) or 12.0))
            min_sl = float(os.getenv("MIN_INITIAL_SL_POINTS", "14"))
            max_sl = float(os.getenv("MAX_INITIAL_SL_POINTS", "32"))
            atr_sl = atr_points * float(os.getenv("ATR_SL_MULTIPLIER", "1.35"))
            regime_floor = 24.0 if regime in ["STRONG_TREND", "HIGH_VOL_OPEN"] else 14.0 if regime in ["SIDEWAYS_CHOPPY", "LOW_ATR_DEAD"] else 18.0
            sl_points = max(min_sl, min(max_sl, max(regime_floor, atr_sl)))
            qty = self.get_compounded_qty(
                trade_data["symbol"],
                self.current_capital + self.today_pnl,
                entry_ltp,
                regime,
                score_smc,
                sl_points,
                trade_data.get("trade_grade", "B"),
                trade_data.get("lot_size"),
                trade_data.get("match_percent", 0.0),
                trade_data.get("day_risk_multiplier", 1.0),
                trade_data.get("regime_stability", 0.0),
            )
            if qty <= 0:
                logger.warning("Entry blocked: one exchange lot exceeds the current risk budget or locked-profit headroom.")
                return False
            order_id = None
            raw_order = None
            entry_execution = {}
            if str(os.getenv("LIVE_TRADING", "FALSE")).strip().upper() == "TRUE":
                params = {
                    "variety": "NORMAL",
                    "tradingsymbol": str(trade_data["symbol"]).strip(),
                    "symboltoken": str(trade_data["token"]).strip(),
                    "transactiontype": "BUY",
                    "exchange": "NFO",
                    "ordertype": "MARKET",
                    "producttype": "INTRADAY",
                    "duration": "DAY",
                    "quantity": str(qty),
                }
                send_epoch = time.time()
                order_id, raw_order = self._place_order_blocking(params)
                response_epoch = time.time()
                entry_ltp = self._resolve_order_fill_price(order_id, entry_ltp)
                fill_epoch = time.time()
                entry_execution = self.execution_monitor.record_fill(
                    trade_data["symbol"], "BUY", "ENTRY", reference_entry_ltp,
                    entry_ltp, trade_data.get("decision_epoch", send_epoch), send_epoch, response_epoch, fill_epoch, order_id,
                )

            lot = int(trade_data.get("lot_size") or (self.LOT_SIZE_BANKNIFTY if "BANKNIFTY" in str(trade_data["symbol"]).upper() else self.LOT_SIZE_NIFTY))
            new_trade = {
                **trade_data,
                "qty": qty,
                "base_lots": max(1, qty // lot),
                "lot_size": lot,
                "entry_prem": entry_ltp,
                "avg_entry_prem": entry_ltp,
                "initial_sl": entry_ltp - sl_points,
                "sl": entry_ltp - sl_points,
                "atr_points": atr_points,
                "target_points": float(trade_data.get("target_points", atr_points * float(os.getenv("ATR_TARGET_MULTIPLIER", "2.2")))),
                "trade_grade": trade_data.get("trade_grade", "B"),
                "max_prem": entry_ltp,
                "min_prem": entry_ltp,
                "pyramided": False,
                "lock_pts": 0.0,
                "entry_time": time.time(),
                "order_id": order_id,
                "raw_order": raw_order,
                "sl_order_id": None,
                "sl_order_raw": None,
                "broker_sl_trigger": 0.0,
                "broker_sl_price": 0.0,
                "sizing_decision": dict(self.last_sizing_decision),
                "entry_execution": entry_execution,
            }
            try:
                sl_order_id, sl_order_raw = self._place_broker_sl_order(new_trade, new_trade["sl"])
                if sl_order_id:
                    new_trade["sl_order_id"] = sl_order_id
                    new_trade["sl_order_raw"] = sl_order_raw
                    new_trade["broker_sl_price"], new_trade["broker_sl_trigger"] = self._sl_price_pair(new_trade["sl"])
            except Exception as exc:
                logger.error(f"Broker-side SL failed after entry. Emergency exit started: {exc}")
                if str(os.getenv("LIVE_TRADING", "FALSE")).strip().upper() == "TRUE":
                    exit_params = {
                        "variety": "NORMAL",
                        "tradingsymbol": str(trade_data["symbol"]).strip(),
                        "symboltoken": str(trade_data["token"]).strip(),
                        "transactiontype": "SELL",
                        "exchange": "NFO",
                        "ordertype": "MARKET",
                        "producttype": "INTRADAY",
                        "duration": "DAY",
                        "quantity": str(qty),
                    }
                    self._place_order_blocking(exit_params)
                raise
            self.active_trades.append(new_trade)
            self.total_trades_today += 1
            if new_trade.get("is_reentry"):
                self.reentries_today += 1
            self._save_state()
            self._save_daily_risk_state()
            return True

    def manage_trade(self, current_spot=None, current_strategy_inputs=None):
        for trade in list(self.active_trades):
            curr_ltp = self.get_premium_ltp(trade["symbol"], trade["token"])
            trade["max_prem"] = max(float(trade["max_prem"]), curr_ltp)
            trade["min_prem"] = min(float(trade.get("min_prem", curr_ltp)), curr_ltp)
            pnl_pts = curr_ltp - float(trade["avg_entry_prem"])

            pyramid_trigger = max(18.0, float(trade.get("atr_points", 12.0)) * 1.5)
            pyramid_quality = (
                trade.get("trade_grade") == "A+"
                and float(trade.get("match_percent", 0.0)) >= float(os.getenv("PYRAMID_MIN_MATCH_PERCENT", "72"))
                and int(trade.get("direction_lead", 0)) >= int(os.getenv("PYRAMID_MIN_DIRECTION_LEAD", "5"))
            )
            if str(os.getenv("ALLOW_PYRAMIDING", "TRUE")).strip().upper() == "TRUE" and pyramid_quality and pnl_pts >= pyramid_trigger and not trade["pyramided"] and (time.time() - trade["entry_time"] > 5):
                self._try_pyramid(trade, curr_ltp)

            trade["sl"] = trailing_stop_logic(trade, curr_ltp, trade["sl"], trade["qty"])
            trade["lock_pts"] = max(0.0, float(trade["sl"]) - float(trade["avg_entry_prem"]))
            if trade.get("sl_order_id"):
                try:
                    self._modify_broker_sl_order(trade, trade["sl"])
                except Exception as exc:
                    logger.error(f"Broker-side SL trail failed for {trade['symbol']}: {exc}")
                    try:
                        cancel_state = self._cancel_broker_sl_order(trade)
                        if cancel_state == "ALREADY_FILLED":
                            self._mark_trade_closed(trade, curr_ltp, reason="BROKER_SL_FILLED")
                            continue
                        trade["sl_order_id"] = None
                        sl_order_id, sl_order_raw = self._place_broker_sl_order(trade, trade["sl"])
                        if sl_order_id:
                            trade["sl_order_id"] = sl_order_id
                            trade["sl_order_raw"] = sl_order_raw
                            trade["broker_sl_price"], trade["broker_sl_trigger"] = self._sl_price_pair(trade["sl"])
                            self._save_state()
                    except Exception as recovery_exc:
                        logger.error(f"Broker-side SL rejection recovery failed for {trade['symbol']}: {recovery_exc}")
                live_qty = self._position_qty_for_symbol(trade["symbol"])
                if live_qty == 0:
                    self._mark_trade_closed(trade, curr_ltp, reason="BROKER_POSITION_FLAT")
                    continue
                if live_qty and live_qty != int(trade["qty"]):
                    trade["qty"] = live_qty
                    self._save_state()
            if time.strftime("%H:%M") >= "15:15":
                self.close_individual_trade(trade, curr_ltp, reason="TIME_SQUARE_OFF")
            elif not trade.get("sl_order_id") and curr_ltp <= float(trade["sl"]):
                self.close_individual_trade(trade, curr_ltp, reason="LOCAL_TRAILING_SL")

    def _try_pyramid(self, trade, curr_ltp):
        lot = int(trade.get("lot_size") or (self.LOT_SIZE_BANKNIFTY if "BANKNIFTY" in str(trade["symbol"]).upper() else self.LOT_SIZE_NIFTY))
        multiplier = max(1, int(os.getenv("PYRAMID_MAX_MULTIPLIER", "2")))
        final_qty = min(trade["base_lots"] * multiplier, self.MAX_EXCHANGE_LOTS) * lot
        extra_qty = final_qty - int(trade["qty"])
        if extra_qty <= 0:
            return
        protected_risk = max(0.0, (float(trade["avg_entry_prem"]) - float(trade["sl"])) * int(trade["qty"]))
        new_risk = max(0.0, (float(curr_ltp) - float(trade["sl"])) * extra_qty)
        wallet = max(1.0, self.current_capital + self.get_net_day_pnl())
        if protected_risk + new_risk > wallet * (float(os.getenv("MAX_PYRAMID_RISK_PERCENT", "4.0")) / 100.0):
            logger.info(f"Pyramid skipped for {trade['symbol']}: portfolio risk budget exceeded.")
            return
        if str(os.getenv("LIVE_TRADING", "FALSE")).strip().upper() == "TRUE":
            params = {
                "variety": "NORMAL",
                "tradingsymbol": str(trade["symbol"]).strip(),
                "symboltoken": str(trade["token"]).strip(),
                "transactiontype": "BUY",
                "exchange": "NFO",
                "ordertype": "MARKET",
                "producttype": "INTRADAY",
                "duration": "DAY",
                "quantity": str(extra_qty),
            }
            self._place_order_blocking(params)
        trade["avg_entry_prem"] = ((trade["avg_entry_prem"] * trade["qty"]) + (curr_ltp * extra_qty)) / final_qty
        trade["qty"] = final_qty
        trade["pyramided"] = True
        trade["sl"] = trade["avg_entry_prem"] + 3.5
        trade["lock_pts"] = 3.5
        if trade.get("sl_order_id"):
            self._modify_broker_sl_order(trade, trade["sl"], force=True)
        self._save_state()

    def close_individual_trade(self, trade, exit_prem, reason="MANUAL_OR_STRATEGY_EXIT"):
        with self._lock:
            if trade not in self.active_trades:
                return
            cancel_state = self._cancel_broker_sl_order(trade)
            if cancel_state == "ALREADY_FILLED":
                self._mark_trade_closed(trade, exit_prem, reason="BROKER_SL_FILLED")
                return
            if str(os.getenv("LIVE_TRADING", "FALSE")).strip().upper() == "TRUE":
                params = {
                    "variety": "NORMAL",
                    "tradingsymbol": str(trade["symbol"]).strip(),
                    "symboltoken": str(trade["token"]).strip(),
                    "transactiontype": "SELL",
                    "exchange": "NFO",
                    "ordertype": "MARKET",
                    "producttype": "INTRADAY",
                    "duration": "DAY",
                    "quantity": str(trade["qty"]),
                }
                send_epoch = time.time()
                exit_order_id, _ = self._place_order_blocking(params)
                response_epoch = time.time()
                reference_exit = float(exit_prem)
                exit_prem = self._resolve_order_fill_price(exit_order_id, exit_prem)
                self.execution_monitor.record_fill(
                    trade["symbol"], "SELL", "EXIT", reference_exit, exit_prem,
                    send_epoch, send_epoch, response_epoch, time.time(), exit_order_id,
                )
            self._mark_trade_closed(trade, exit_prem, reason=reason)

    def _write_trade_journal(self, trade, exit_prem, trade_pnl, reason):
        try:
            os.makedirs("logs", exist_ok=True)
            entry = float(trade["avg_entry_prem"])
            payload = {
                "closed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": trade.get("symbol"),
                "type": trade.get("type"),
                "qty": int(trade.get("qty", 0)),
                "entry": entry,
                "exit": float(exit_prem),
                "pnl": float(trade_pnl),
                "exit_reason": reason,
                "grade": trade.get("trade_grade", "B"),
                "signal_score": trade.get("signal_score", 0),
                "signal_available": trade.get("signal_available", 0),
                "match_percent": trade.get("match_percent", 0.0),
                "direction_lead": trade.get("direction_lead", 0),
                "mfe_points": float(trade.get("max_prem", entry)) - entry,
                "mae_points": float(trade.get("min_prem", entry)) - entry,
                "pyramided": bool(trade.get("pyramided", False)),
                "initial_sl": float(trade.get("initial_sl", 0.0)),
                "final_sl": float(trade.get("sl", 0.0)),
                "day_type": trade.get("day_type", "UNKNOWN"),
                "matched_conditions": list(trade.get("matched_conditions", [])),
                "condition_points": list(trade.get("condition_points", [])),
                "sizing_decision": trade.get("sizing_decision", {}),
                "entry_execution": trade.get("entry_execution", {}),
            }
            with open(os.path.join("logs", "trade_journal.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
        except Exception as exc:
            logger.error(f"Trade journal write failed: {exc}")

    def _refresh_trade_analytics(self):
        try:
            from trade_analytics import analyze_trades
            analyze_trades()
        except Exception as exc:
            logger.error(f"Trade analytics refresh failed: {exc}")

    def _mark_trade_closed(self, trade, exit_prem, reason="UNKNOWN"):
        if trade not in self.active_trades:
            return
        trade_pnl = (float(exit_prem) - float(trade["avg_entry_prem"])) * int(trade["qty"])
        self.today_pnl += trade_pnl
        self.last_exit_time = time.time()
        if trade.get("type", "CE") == "CE":
            self.total_ce_pnl += trade_pnl
        else:
            self.total_pe_pnl += trade_pnl
        self.current_consecutive_losses = self.current_consecutive_losses + 1 if trade_pnl < 0 else 0
        trailing_reasons = {"BROKER_SL_FILLED", "BROKER_POSITION_FLAT", "LOCAL_TRAILING_SL"}
        self.last_closed_trade = {
            "symbol": trade.get("symbol"),
            "type": trade.get("type"),
            "regime": trade.get("regime"),
            "exit_reason": reason,
            "pnl": trade_pnl,
            "lock_pts": float(trade.get("lock_pts", 0.0)),
            "profitable_trailing_exit": reason in trailing_reasons and trade_pnl > 0 and float(trade.get("lock_pts", 0.0)) > 0,
            "closed_epoch": time.time(),
        }
        self._write_trade_journal(trade, exit_prem, trade_pnl, reason)
        threading.Thread(target=self._refresh_trade_analytics, name="TradeAnalyticsRefresh", daemon=True).start()
        self.active_trades.remove(trade)
        self._save_state()
        self._save_daily_risk_state()

    def get_dashboard_payload(self):
        rows = []
        live_pnl = 0.0
        for trade in list(self.active_trades):
            try:
                curr_ltp = self.get_premium_ltp(trade["symbol"], trade["token"])
            except Exception:
                curr_ltp = float(trade["max_prem"])
            pnl = (curr_ltp - float(trade["avg_entry_prem"])) * int(trade["qty"])
            live_pnl += pnl
            rows.append({
                "symbol": trade["symbol"],
                "type": trade.get("type", "CE"),
                "grade": trade.get("trade_grade", "B"),
                "qty": trade["qty"],
                "entry": float(trade["avg_entry_prem"]),
                "ltp": curr_ltp,
                "initial_sl": float(trade["initial_sl"]),
                "trail_sl": float(trade["sl"]),
                "target_points": float(trade.get("target_points", 0.0)),
                "trail_buffer": float(trade.get("trail_buffer", 0.0)),
                "locked": max(0.0, float(trade["sl"]) - float(trade["avg_entry_prem"])),
                "broker_sl": float(trade.get("broker_sl_trigger") or 0.0),
                "sl_order_id": trade.get("sl_order_id") or "",
                "pnl": pnl,
            })
        authoritative_net_pnl = self.get_net_day_pnl()
        portfolio_greeks = {
            "delta": sum(float(trade.get("option_delta", 0.0)) * int(trade.get("qty", 0)) for trade in self.active_trades),
            "gamma": sum(float(trade.get("option_gamma", 0.0)) * int(trade.get("qty", 0)) for trade in self.active_trades),
            "theta": sum(float(trade.get("option_theta", 0.0)) * int(trade.get("qty", 0)) for trade in self.active_trades),
            "vega": sum(float(trade.get("option_vega", 0.0)) * int(trade.get("qty", 0)) for trade in self.active_trades),
        }
        return {
            "executed_trades_list": rows,
            "broker_positions_list": self.get_broker_positions_rows(),
            "total_pnl": authoritative_net_pnl,
            "booked_pnl": self.today_pnl,
            "current_capital": self.current_capital + authoritative_net_pnl,
            "daily_peak_pnl": self.daily_peak_pnl,
            "daily_profit_floor": self.daily_profit_floor,
            "daily_halt": self.daily_halt,
            "portfolio_greeks": portfolio_greeks,
            "execution_quality": self.get_execution_summary(),
            "last_sizing_decision": self.last_sizing_decision,
        }
