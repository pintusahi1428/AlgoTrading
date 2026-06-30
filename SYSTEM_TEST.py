import os

os.environ["LIVE_TRADING"] = "TRUE"
os.environ["BROKER_SL_ORDER"] = "TRUE"
os.environ["LOT_SIZE_NIFTY"] = "1"
os.environ["MAX_EXCHANGE_LOTS"] = "1"
os.environ["MAX_TRADE_PER_DAY"] = "10"
os.environ["INITIAL_CAPITAL"] = "25000"
os.environ["RECOVER_OPEN_TRADES"] = "FALSE"
os.environ["RECOVER_DAILY_RISK"] = "FALSE"
os.environ["BROKER_PNL_AUTHORITATIVE"] = "FALSE"
os.environ["STRONG_REENTRY_MIN_SECONDS"] = "0"

import trade_manager as trade_manager_module
from trade_manager import TradeManager


class MockBroker:
    def __init__(self):
        self.next_order_id = 1000
        self.events = []
        self.option_ltp = 100.0
        self.net_qty = 1
        self.reported_pnl = None

    def rmsLimit(self):
        return {"status": True, "data": {"net": "25000"}}

    def ltpData(self, exchange, symbol, token):
        return {"status": True, "data": {"ltp": self.option_ltp}}

    def placeOrder(self, params):
        self.next_order_id += 1
        order_id = str(self.next_order_id)
        self.events.append(("PLACE", params["ordertype"], order_id, dict(params)))
        return order_id

    def orderBook(self):
        return {
            "status": True,
            "data": [{
                "orderid": "1001",
                "status": "complete",
                "averageprice": "100.25",
            }],
        }

    def modifyOrder(self, params):
        self.events.append(("MODIFY", params["orderid"], dict(params)))
        return {"status": True, "data": {"orderid": params["orderid"]}}

    def cancelOrder(self, order_id, variety):
        self.events.append(("CANCEL", order_id, variety))
        return {"status": True}

    def position(self):
        return {
            "status": True,
            "data": [{
                "tradingsymbol": "NIFTYTESTCE",
                "netqty": str(self.net_qty),
                "buyavgprice": "100.25",
                "sellavgprice": "0",
                "ltp": str(self.option_ltp),
                "pnl": str(self.reported_pnl if self.reported_pnl is not None else (self.option_ltp - 100.25) * self.net_qty),
                "producttype": "INTRADAY",
            }],
        }


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    broker = MockBroker()
    manager = TradeManager(broker)
    real_strftime = trade_manager_module.time.strftime
    def fake_strftime(fmt, *args):
        if fmt == "%Y-%m-%d":
            return "2026-06-24"
        if fmt == "%H:%M":
            return "10:00"
        return real_strftime(fmt, *args)

    trade_manager_module.time.strftime = fake_strftime
    try:
        opened = manager.open_trade({
            "index_name": "NIFTY",
            "symbol": "NIFTYTESTCE",
            "token": "99999",
            "side": "BUY",
            "type": "CE",
            "regime": "STRONG_TREND",
            "smc_score": 80,
        })
        assert_true(opened, "Entry accepted")
        assert_true(len(manager.active_trades) == 1, "Trade registered after broker entry")
        trade = manager.active_trades[0]
        assert_true(trade["entry_prem"] == 100.25, "Broker average fill used as entry")
        assert_true(bool(trade["sl_order_id"]), "Broker-side SL order placed")
        assert_true(not trade.get("sizing_decision", {}).get("blocked", True), "Dynamic confidence/volatility sizing decision recorded")

        broker.option_ltp = 122.0
        manager.manage_trade()
        assert_true(trade["sl"] >= 105.25, "Multi-level profit locking moved SL to entry plus 5")
        assert_true(
            any(event[0] == "MODIFY" for event in broker.events),
            "Broker-side SL order modified while trailing",
        )

        broker.option_ltp = 130.25
        manager.manage_trade()
        assert_true(trade["sl"] >= 123.25, "At plus 30 points SL locks plus 23 points")
        broker.option_ltp = 140.25
        manager.manage_trade()
        assert_true(trade["sl"] >= 128.25, "After plus 30 SL follows peak with a 12-point gap")

        manager.close_individual_trade(trade, 128.25, reason="LOCAL_TRAILING_SL")
        assert_true(len(manager.active_trades) == 0, "Trade removed after confirmed exit")
        cancel_index = next(i for i, event in enumerate(broker.events) if event[0] == "CANCEL")
        exit_index = max(i for i, event in enumerate(broker.events) if event[0] == "PLACE" and event[1] == "MARKET")
        assert_true(cancel_index < exit_index, "SL cancelled before market exit")
        assert_true(manager.today_pnl > 0, "Closed trade PnL booked")
        execution = manager.get_execution_summary()
        assert_true(execution["fills"] >= 2, "Entry and exit execution quality recorded")
        qualified, _ = manager.evaluate_same_direction_reentry(
            "CE",
            "STRONG_TREND",
            {"side": "BUY", "match_percent": 80.0, "direction_lead": 6},
            {"momentum": 1, "micro_trend_direction": "BULLISH", "vwap_bounce": "AWAY_FROM_VWAP"},
        )
        assert_true(qualified, "Profitable strong-trend trailing exit permits guarded same-direction re-entry")

        manager.today_pnl = 5000.0
        shield = manager.enforce_daily_equity_shield()
        assert_true(shield["locked_floor"] == 3750.0, "INR 5,000 peak locks INR 3,750 day floor")
        manager.today_pnl = 3700.0
        shield = manager.enforce_daily_equity_shield()
        assert_true(shield["halted"], "Daily equity shield halts after locked floor breach")
        allowed, _ = manager.can_open_new_trade()
        assert_true(not allowed, "New entries remain blocked after daily profit lock exit")

        second = TradeManager(broker)
        second.today_pnl = 10000.0
        shield = second.enforce_daily_equity_shield()
        assert_true(shield["locked_floor"] == 8750.0, "INR 10,000 peak locks INR 8,750 day floor")

        os.environ["BROKER_PNL_AUTHORITATIVE"] = "TRUE"
        broker.reported_pnl = 5000.0
        third = TradeManager(broker)
        shield = third.enforce_daily_equity_shield()
        assert_true(shield["locked_floor"] == 3750.0, "Broker-reported day PnL drives the profit floor")
        broker.reported_pnl = 3600.0
        shield = third.enforce_daily_equity_shield()
        assert_true(shield["halted"], "Broker-reported giveback triggers the daily halt")
        os.environ["BROKER_PNL_AUTHORITATIVE"] = "FALSE"
        print("\nSYSTEM TEST RESULT: ALL CHECKS PASSED")
    finally:
        trade_manager_module.time.strftime = real_strftime


if __name__ == "__main__":
    main()
