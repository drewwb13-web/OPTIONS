# APEX / SATY / PROP — PROJECT CLOSE-OUT

This file records where the research finished, written so a fresh session can pick it up
without re-deriving anything. The working files live on Drew's machine in
`C:\Users\drew_\OneDrive\Desktop\downloads\WORK_2026_10\`: `FINAL_PLAN.md`,
`DISCOVERY_REPORT.md`, `RESEARCH_LEDGER.csv` and the round reports.

Constraints throughout: $5,000 total capital, trades NQ/MNQ, uses Lucid Trading for prop
evaluations, TradingView free plan (alerts work, data export does not), maximum loss
tolerance not yet decided.

## Verdict per strategy

| Strategy | Verdict | Key evidence |
|---|---|---|
| Vol-target core (QQQ, 200-day filter, TARGET_VOL 0.15, LEV_CAP 2.0) | **KEEP** | 12.14%/yr over 100 years, max drawdown -33.69%, Sharpe 0.91 for the rule as run (1% band, 0.25x rebalance threshold, cash interest, expense ratio, financing charged). Nothing is fitted. The earlier ~12.3% / -32% figure used a different year-count convention. |
| Core leverage dial (TARGET_VOL) | **0.15 kept** | 100-year: 0.15 -> 12.14% / -33.69% / Sharpe 0.91; 0.20 -> 13.82% / -40.22% / 0.86; 0.25 -> 15.21% / -42.36% / 0.85. QLD era 2006+: 0.15 -> 11.26% / -20.1%; 0.20 -> 14.00% / -26.4%; 0.25 -> 15.95% / -32.2%. More dial = more growth and deeper drawdowns, lower Sharpe. QLD and margin give near-identical results once the rebalance threshold is in place. |
| B5 Connors RSI(2) on SPY/QQQ (close > 200-day, RSI(2) < 10, exit when close > 5-day SMA) | **PAPER** | +$23.09/trade, ~$601/yr on $5,000, t 8.04. Edge halved after publication (+0.746%/trade before 2008, +0.382% after). Beats raising the core dial on Sharpe; core + B5 max drawdown -28.21%. Fails only the deflated Sharpe, by 0.005. Kill if the average trade is below 0 after 30 forward trades. |
| Williams volatility breakout (B1b) | RETIRED | Failed the pre-registered rules. |
| LucidFlex 50K, 1 account, coin flip, fixed 60/84 eval bracket | **DROPPED by Drew (2026-10-20)** | +$544/yr (2022 to mid-2024) up to +$3,989/yr (mid-2024 to 2026) at Drew's confirmed $90 evaluation / $90 reset. The profit comes from entries at the open, i.e. it pays off on movement. |
| Multi-account prop (independent coin flips) | RETIRED | Accounts take opposite sides on 24-35% of days, which is Lucid's banned hedging (automated detection). Same-direction versions lose money with 27-54% ruin. |
| ES/SPX APEX breakout engine (V40/V41) | RETIRED | -0.17 pts/trade even before costs, worse than random direction. 0 of ~37,000 settings significant in both halves. |
| Saty Phase Oscillator as a trade (dot, fall618, filters) | RETIRED | Best near-miss: fall618 / hold 10 days, placebo rank 96.9% vs 99% needed, and it failed on the S&P. |
| Saty as LEAPS entry timing | RETIRED | Buying on a fixed quarterly schedule beat every Saty trigger. Signal days sit in the bottom 0.1-14.9% of random days because implied volatility is high on them. |
| Saty ATR Levels / Pivot Ribbon / Volume Stack | RETIRED | 0 of 10 pass. The ATR trigger breakout is a significant *loser* in every era (t -2.2 to -3.9). |
| Intraday direction features (EMA/VWAP/MACD/RSI/Saty/opening bar, 5m to daily) | RETIRED | 0 of 51 single features and 0 of 8 combinations survive Holm correction; 0 of 3 beat the coin flip on the holdout. |
| Percentage-of-price prop brackets | RETIRED | +$551/yr vs +$1,785/yr for the fixed-point bracket. Lucid's rules are set in dollars. |
| 0DTE premium, intraday momentum, overnight drift, calendar effects, prop day-selection, prop playbook changes, diversified core, mirrored breakout (final round) | RETIRED | 0 passed. 0DTE condors win 81-86% but need 90.1% to break even. Every prop day-filter cut profit, because the prop book earns from how many evaluations it gets through. Keep the current prop playbook (K0). |
| Intermarket signals (credit, VIX term structure, dollar, copper/gold, rates, oil), F13 | RETIRED | 0 of 8 pass as a core filter; none predict next-5-day QQQ returns (Newey-West t between -2.48 and +1.81). |
| Regime atlas (dollar, rates, yield curve, credit, VIX, luxury vs needs, housing, goods, transports, volume, trend), F14 | REFERENCE ONLY | 1 of 90 conditions STABLE: capitulation volume (SPY volume > 2x its 50-day average on a down day) is followed by S&P returns +3.0% higher over 63 days, with a worse worst-5%. VIX > 25 (Nasdaq 63-day +6.1%) came close. Nearly every condition moves 21-day P(up) only between 57% and 73%, against a ~64% base rate. |
| QQQ 3-day dip buy in uptrend + low volatility (P-D), F14 | **PAPER** | Validation t 2.59, lockbox t 2.94, regime-matched placebo 99.4% in both windows, positive at 2x costs. Fails only the deflated Sharpe (0.34 / 0.55 vs 0.95). Negative before 2006. At 1% risk it makes about $60-80/yr on $5,000, roughly 9 trades a year. |
| Gamma regime, panic sizing, signal combination, COT retest, prop on ES, sector rotation, SVXY (F16-F22) | 0 of 20 pass; 3 NEAR-MISSES go to forward testing | G2 0DTE put spread (0.10 delta, 10:00 ET) only on positive-gamma days: profitable in both eras even at worst-side (bid/ask) fills, lockbox t 3.69, placebo 100%, but needs a >=95.2% win rate and got 95.5% / 97.6%. G4 follow the NQ opening move on negative-gamma days: positive in both eras and at 2x costs, lockbox t 1.39. W1 equal-weight signal score: beat the core's Sharpe in both windows (0.89 vs 0.76, 1.19 vs 0.94) with a smaller drawdown, but in the market only 28% of days. |

## Yardstick for any future intraday backtest

A planted one-bar look-ahead scores **t = 30-58**. Clean multi-timeframe features score
**t < 1.5**. Any intraday strategy reporting a strong direction edge should be assumed to
leak until proven otherwise.

## Bug classes found in this project (check new code for each)

1. Labels whose forward window crosses into the next trading day.
2. Threshold tuned on the same rows the model was fit on; precision_recall_curve off-by-one.
3. Close-only exits that hide intraday stop-outs; entry and exit on the same bar.
4. Timezone joins (ET vs CT) that still "match" ~85% of rows, so a coverage-% check cannot catch them.
5. Strike-level option tapes merged per minute, multiplying the rows.
6. SPX option strikes compared with ES prices without the ES-SPX price gap (~29 pts).
7. Fixed-dollar costs applied to old, low-price years; a PF above 1 next to a negative $ total is the tell.
8. Contract-roll handling: difference adjustment drives prices negative, and roll detection that picks overnight moves. Use ratio adjustment, with roll gaps taken from the NQ minus NDX basis.
9. Filters that silently fail to apply, so the "filtered" result equals the unfiltered one.
10. A derived position size of zero treated as a blown account (phantom fees).
11. Duplicated simulation loops, so a fix lands in only one copy.
12. Selection rules that reward doing nothing when every option is negative.
13. Higher-timeframe indicators read from the still-forming bar.
14. Independent random directions across prop accounts = hedging.
15. Pass tests that compare floats with a strict `>`: a 1e-16 difference once printed PASS. Require a material difference.
16. Missing data read as "condition false" (NaN > x is False): it fabricated decades of fake states before a series existed.
17. t-statistics on near-zero variance (every trade hitting the same stop): t = -1624 is a divide-by-epsilon, not evidence.
18. Rules judged on a handful of trades: report them as untestable instead.

## Final step

One final round finishes the project:
- corrected deflated-Sharpe rule;
- any discovery families not yet run;
- F8 0DTE premium;
- F9 volatility timing for the prop evaluation;
- F10 one post-hoc candidate, paper trading only;
- F11 the prop playbook (how the account is traded against Lucid's rules);
- F12 the vol-target core diversified across QQQ / bonds / gold;
- close-out files, including a "what was not tested and why" list: single stocks, crypto,
  forex, NQ-ES spreads, news/sentiment and order-book data (no data on hand).

After that round, **historical searching on THIS data stops** (the search itself continues with forward testing and new data sources). The only new evidence is
forward paper/live results, reviewed on **2027-01-18** against the expected ranges in
`PAPER_PLAN.md`.

## F23 result (2026-10-21)

The 8 videos teach an UNUSUAL-VOLUME (expansion) breakout scan, not contraction-to-expansion, and there are no performance claims. On his own named tickers the rule's 20-day median return is -1.9% (positive means come from single outliers such as the AMC squeeze). On S&P 500 stocks it was strong before 2016 only because of survivorship bias, then flat or negative; 0 of 24 variant-universe tests pass. Not tested: his preferred $300M-$2B small caps, because no survivorship-free small-cap data is on hand.

Side findings: the core's TARGET_VOL is 0.15 everywhere. G2's real live structure (XSP 1-wide = SPX 10-wide) stays positive at true 2x costs in both eras, unlike the tested 5-wide. G4 risks more than $100 per MNQ on 58% of trades (max $826), which is large for a $5,000 account.

**Correction (2026-10-21):** the first report of this finding mislabelled the columns. Its "2x" figures (+$4,037 / +$7,775) were base cost, and its "net" figures were gross. Corrected totals per SPX-size spread (divide by 10 for one XSP spread):

| Structure | Window | Gross | Base cost | True 2x cost |
|---|---|---|---|---|
| SPX 10-wide | DEV | +$7,397 | +$4,037 | +$677 |
| SPX 10-wide | LOCKBOX | +$11,625 | +$7,775 | +$3,925 |
| SPX 5-wide | DEV | +$2,145 | -$1,185 | -$4,515 |
| SPX 5-wide | LOCKBOX | +$4,743 | +$983 | -$2,777 |

Per XSP spread that is about +$12.62 to +$27.77 a month at base cost. The worst month was -$102, and the maximum drawdown -$216. The 2x margin in DEV is thin.

## F27-F29 results (2026-10-21)

| Family | Verdict | Key evidence |
|---|---|---|
| F27 free stock-anomaly pre-screen (Ken French + OSAP, long-only top portfolio minus market) | 0 of 7 pass, so **no Sharadar purchase** | Momentum, industry momentum, 52-week high, SUE, announcement return, short-term reversal and small x momentum each earned +6.6% to +10.8%/yr in 1963-2004 and about nothing after. The best post-2004 t was 1.18; the break-even cost was at most 2.2%/yr. |
| F28 B5 on 13 ETFs | **RE-CHECK** | Passes (a), (b), (c) and (e); fails DSR, like B5. Failed clause (f), "more $ with no worse drawdown", but $/yr divided by max drawdown is better than B5's in every era (0.65 vs 0.57, 0.64 vs 0.48, 1.67 vs 1.12). Clause (f) was worded badly, so it is re-judged at matched risk. About 45 trades/yr vs about 12 for B5. |
| F29 core rule on Bitcoin | RETIRED | Beat the core's Sharpe in every window, but placebo rank was 72-88%: a circular shift of its own leverage series earned nearly as much. The gain is exposure to BTC's 54%/yr rise, not timing. |

**F28 re-check (2026-10-22): does not beat B5.** The 13-ETF book was scaled to 0.2027x, chosen on DEV only to match B5's drawdown. Its Sharpe is higher in both later eras (VAL 0.34 vs 0.22, LOCK 0.67 vs 0.63), but LOCKBOX $/yr is $177 vs $183, and the rule needed both. Drawdown is not monotone in position size, because cash limits how many trades are open at once. B5 stays SPY/QQQ only.

**F30 B5 traded with options, screen only (Black-Scholes, one IV per day from the action-day close VIX/VXN, no skew).** Implied volatility falls 1.1-1.8 points between entry and exit.
- V1, buying a ~0.50-delta call (30+ DTE), loses to plain shares in every era. Option edge t is -10.7 to -16.8. **RETIRED.**
- V2, selling a ~0.30-delta 5-wide put spread (7+ DTE), passes the lenient gate: option edge +$6.05/trade (t 2.06) in VAL and +$17.84 (t 4.24) in LOCK, and positive at 2x costs. But DEV is negative (edge t -2.63, 2x total -$2,483), and DEV max drawdown is -$3,657, too big for $5,000.
- The result depends on the IV convention: two earlier, wrong conventions gave the opposite sign. Real quotes are needed.
- Drew's ThetaData account is the free tier: EOD quotes from 2024-01-02 only. Standard is $80/month, history from 2016. The subscriber agreement requires deleting the data within 30 days of cancelling.

**F30B real quotes (2026-10-22): V2 RETIRED.** Tested on 124 real quoted trades, 2023-08 to 2026-10, from the free ThetaData tier.
- On real XSP quotes the bid-ask spread is 202% of the credit collected, and the trade makes -$0.56 per trade. Priced as SPXW / 10, it showed +$39.77.
- The Black-Scholes screen tracked real P&L (correlation 0.71) but overstated it by $31.69 per trade (t -4.33).
- **The same scaling (SPX / 10) priced G2 for XSP, so G2 must be re-checked on real XSP quotes.**

**B5 inside one account:** B5's $23.09/trade and $601/yr were margin numbers (2.0x cap). Capped at cash, they are $20.47 and $533. B5 fires when the core is already fully invested: median core leverage on B5 signal days is 0.98x, and 44.8% of signal days leave no cash. Inside one $5,000 cash account with no margin, B5 earns $94 / -$19 / $20 a year (DEV / VAL / LOCK).

**F31:** FOMC days are G2's best days ($5.13/trade, 100% win rate, vs $1.42 on other days). The filter "skip G2 on FOMC and CPI days" is rejected.

**2026-10-23 round:**
- **F31 pre-FOMC drift:** FAIL (VAL t 0.64, LOCK negative, placebo 72.7%).
- **F32 macro regimes:** no regime state lost money for any of our books. Two rotation strategies were tested:
  - V1, an Investment Clock rotation (2004 mapping), failed;
  - V2, the core moved to cash in stagflation, failed.

  Both had a worse Sharpe than the plain core in VALIDATION and LOCKBOX.
- **G2 vehicle:** CBOE publishes a real XSP chain. In the one snapshot taken, XSP's bid-ask spread was 133% of the credit, against 48% for SPXW / 10, while G2 can absorb only $1.20-2.02 of extra slippage per spread. XSP looks non-viable; the G2X_* forward keys will settle it. The SPX 10-wide version is possible from about $20k: $126-278/month at base cost, $21-140/month at 2x, max drawdown -$1,470 to -$2,626.
- **Capital plan, one margin account:** adding every paper strategy to the core raises return about 22% and drawdown about 60% (VAL at 2x on $5k: core alone $749 at -20.1%, everything $914 at -32.2%). Only the core is proven.

The first G2 forward trade was voided because pre-edit code opened an SPX 5-wide spread and the close job priced it as an XSP 1-wide. The wing width now comes from the logged strikes, and a mismatch voids the trade.

## Queue after F23

- F26 stock momentum (M1-M4), pre-registered in `RULES_F26.md`. It needs survivorship-free stock data, so it runs only after a data purchase.
- Before buying: a free pre-screen of published stock anomalies, using Ken French and Open Source Asset Pricing returns, which are survivorship-free.
- B5 extended to more ETFs, and the core rule applied to Bitcoin. Both are free.

## Earlier queue (done)

F23: a YouTube trader's daily volume contraction-to-expansion method. Extract his rules from the video transcripts, check the trade examples he shows, then test the rules mechanically on ETFs and on point-in-time S&P 500 / Nasdaq-100 stocks, with placebos and the corrected DSR.
