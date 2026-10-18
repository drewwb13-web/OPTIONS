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
| Vol-target core (QQQ, 200-day filter, TARGET_VOL 0.15, LEV_CAP 2.0) | **KEEP** | ~12.3%/yr over 100 years, max drawdown -32%, t = +7.08, financing costs charged. Nothing is fitted. |
| LucidFlex 50K, 1 account, coin flip, fixed 60/84 eval bracket | **KEEP, small** | +$544/yr (2022 to mid-2024) up to +$3,989/yr (mid-2024 to 2026) at Drew's confirmed $90 evaluation / $90 reset. The profit comes from entries at the open, i.e. it pays off on movement. |
| Multi-account prop (independent coin flips) | RETIRED | Accounts take opposite sides on 24-35% of days, which is Lucid's banned hedging (automated detection). Same-direction versions lose money with 27-54% ruin. |
| ES/SPX APEX breakout engine (V40/V41) | RETIRED | -0.17 pts/trade even before costs, worse than random direction. 0 of ~37,000 settings significant in both halves. |
| Saty Phase Oscillator as a trade (dot, fall618, filters) | RETIRED | Best near-miss: fall618 / hold 10 days, placebo rank 96.9% vs 99% needed, and it failed on the S&P. |
| Saty as LEAPS entry timing | RETIRED | Buying on a fixed quarterly schedule beat every Saty trigger. Signal days sit in the bottom 0.1-14.9% of random days because implied volatility is high on them. |
| Saty ATR Levels / Pivot Ribbon / Volume Stack | RETIRED | 0 of 10 pass. The ATR trigger breakout is a significant *loser* in every era (t -2.2 to -3.9). |
| Intraday direction features (EMA/VWAP/MACD/RSI/Saty/opening bar, 5m to daily) | RETIRED | 0 of 51 single features and 0 of 8 combinations survive Holm correction; 0 of 3 beat the coin flip on the holdout. |
| Percentage-of-price prop brackets | RETIRED | +$551/yr vs +$1,785/yr for the fixed-point bracket. Lucid's rules are set in dollars. |

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

After that round, **historical searching on this data stops**. The only new evidence is
forward paper/live results, reviewed on **2027-01-18** against the expected ranges in
`PAPER_PLAN.md`.
