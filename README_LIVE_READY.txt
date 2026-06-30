COMPLETE PACKAGE - MASTER SNIPER v11.0

This ZIP contains the core files and .env needed to start the updated bot.
Extract it into C:\Users\Administrator\AlgoTrading and run RUN_TESTS.bat first.
The token master downloads automatically when token_master.json is absent.

IMPORTANT: .env contains private broker credentials. Do not share this ZIP.

Before running:

1. Run RUN_TESTS.bat before market open.
2. Start using AUTO_START_BOT.bat only after all tests pass.
3. Rotate the credentials previously exposed in chat as soon as practical.

Tests before live market:

python SYSTEM_TEST.py
python SIGNAL_BACKTEST.py

SYSTEM_TEST checks entry, broker average fill, broker-side SL, trailing SL
modification, SL cancellation before exit, and booked PnL.

SIGNAL_BACKTEST replays historical_data.csv. It validates signal activity only.
Actual option PnL needs historical option premium, IV, OI, and option-chain data.

Important behavior:

- No dummy index price is used.
- No dummy option premium is used.
- Mandatory layers are self-healing first: live-price retry, token/session refresh, nearest real option token fallback, premium LTP retry, and broker order retry.
- If mandatory live data, token, premium LTP, or broker order still fails after self-healing, entry is blocked as the final safety gate.
- If optional option-chain/IV/PCR style factors fail temporarily, the bot does not invent fake values. It gives those factors zero credit and continues using the remaining real factors.
- Recent real option-chain data is cached for up to 5 minutes to handle short broker/API interruptions.
- Active trades are shown in separate rows with entry, current premium, initial SL, trailing SL, locked points, and live PnL.
- Rejected broker orders are not added to active trades.
- After a live entry, the bot places a broker-side SELL STOPLOSS_LIMIT order.
- When trailing SL rises, the bot modifies the broker-side SL order so it is visible in the broker app.
- If broker-side SL placement fails after entry, the bot sends an emergency market exit instead of leaving the position unprotected.
LIVE FACTORS ADDED
------------------
1. Option IV percentile
   Primary: Angel One optionGreek API.
   Fallback: implied volatility solved from real Angel option LTP, NIFTY spot,
   strike and expiry. RISK_FREE_RATE is a configurable model input.

2. Gift Nifty
   Source: official NSE IX tokenized API. The bot checks both the near-month
   graph header and derivatives-watch endpoints.

3. NIFTY 50 advance/decline ratio
   Source: official NSE allIndices API, with the official NIFTY 50 constituent
   endpoint as fallback.

4. Top weighted confirmation
   Source: Angel One FULL market quotes for the configured NIFTY stocks.
   Direction is based on each stock's real change from previous close and the
   configured TOP_NIFTY_WEIGHTS.

INSTITUTIONAL ADD-ONS ADDED ON 2026-06-27
-----------------------------------------
5. Real Greeks: Delta, Gamma, Theta and Vega.
   Primary: Angel One optionGreek API.
   Fallback: Black-Scholes Greeks calculated from real Angel option LTP,
   real NIFTY spot, strike and expiry. Fixed dummy Greeks are not used.

6. Dealer Position.
   Source: real option OI plus live Greeks. The bot estimates dealer delta,
   gamma, vega and hedge-pressure direction.

7. Max Pain.
   Source: real option-chain OI across nearby strikes.

8. Top-15 weighted breadth refined.
   Source: Angel One FULL quotes. If TOP_NIFTY_* env values are not present,
   the bot auto-resolves the top-15 NIFTY stock tokens from token_master.json.

These four institutional factors are included in signal scoring and also shown
on the live dashboard.

PROFIT SHIELD AND CONSENSUS BUILD ADDED ON 2026-06-28
-----------------------------------------------------
- Maximum six entries per day. This is a cap, not a promise that six trades will occur.
- BUY and SELL are scored independently across up to 41 available checks.
- Entry requires data coverage, match percentage, direction lead, critical confirmations and limited conflicts.
- The live loop uses real one-second Angel LTP snapshots for tick pressure, acceleration, micro trend and price efficiency. This is not exchange-colocated Level-2 HFT.
- Daily equity shield starts after INR 2,000 peak PnL. Below INR 5,000 it locks 60% of peak; from INR 5,000 onward it permits only INR 1,250 giveback.
- INR 5,000 peak creates an INR 3,750 floor. INR 10,000 peak creates an INR 8,750 floor.
- When net PnL touches the floor, open bot positions are market-exited and new entries stay blocked for that day.
- Gap moves, slippage, rejected orders, outages and exchange conditions can cause the actual exit below the displayed floor.
- Pyramiding is limited to A+ signals with at least 72% consensus and a risk-budget check.
- Fixed BOS, CHOCH and FVG point offsets and index-price regime switches were replaced by rolling volatility-normalized thresholds.

Run CHECK_LIVE_FACTORS.bat before market start. A temporarily unavailable
optional factor receives zero score; it is never replaced with invented market
data. Mandatory price, option token, premium, entry order and broker-SL checks
remain protected by the existing execution gates.

No strategy or backtest can guarantee a profit. Start with the smallest allowed
size and verify orders, quantities and broker-side stop orders in the broker app.

MERGED TRAILING AND RE-ENTRY UPDATE (2026-06-29)
------------------------------------------------
- Both supplied ZIP files were audited. See MERGE_AUDIT_REPORT.txt.
- At +30 option premium points, SL locks +23 points.
- Thereafter SL follows the highest premium with a 12-point gap and never moves down.
- A profitable trailing exit may re-enter the same direction only when the strong trend,
  fresh 72% consensus, direction lead and pullback-recovery checks all pass.
- Re-entry is limited to two per day and still counts toward the six-trade daily cap.
- CE BUY and PE BUY use the same 41 conditions and equal one-point weights.
- Run SHOW_SIGNAL_POINTS.bat while the live bot is warmed up to see each condition's points.
- CONDITION_POINT_MAP.txt lists all 41 equal-weight CE/PE conditions.

ADVANCED NON-INDICATOR MODULES (2026-06-29)
-------------------------------------------
- RSI, MACD, Supertrend and EMA Cross have been removed from both calculation and scoring.
- RegimeDetectionEngine classifies Trend Day, Range Day, Expiry Day and Gap Day, then adjusts
  entry strictness and position risk without adding another conventional indicator.
- Walk-forward optimization checks weekly while the bot runs. It needs at least 40 real closed
  trades and can change only bounded MIN_MATCH_PERCENT and MIN_DIRECTION_LEAD values.
- Trade analytics records win/loss reasons and realized factor attribution. Run TRADE_ANALYTICS.bat.
- Dynamic sizing combines capital, stop risk, volatility regime, day type, consensus confidence
  and regime stability. It never creates fractional exchange lots.
- Execution monitoring records reference price, fill price, adverse slippage, response/fill
  latency, missed actionable signals and a sample-dependent rating. Run EXECUTION_QUALITY_REPORT.bat.
- Ratings marked INSUFFICIENT_DATA are intentional; no reliable rating is produced from a tiny sample.
- Closed-market mode pauses institutional factor polling, preventing repeated optionGreek
  AB9019 noise when the exchange has no current Greeks data.
