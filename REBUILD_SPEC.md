# REBUILD_SPEC — the core engine, as recorded (2026-11-26)

The local project folder was lost when the Windows SSD failed. This file collects every core-engine
detail found in the record (PROJECT_CLOSEOUT.md, HANDOVER.md) and in the past round reports, so the
engine can be rebuilt and checked against the published numbers. Paper only.

## The rule
- **Trend filter:** QQQ close vs its 200-row simple moving average, with a 1% band.
  OUT when the close is below 0.99 x SMA200; back IN when it is above 1.01 x SMA200; otherwise keep the
  previous state. OUT means target leverage 0.
- **Leverage:** L = TARGET_VOL / (60-row realized volatility of daily QQQ returns, annualized), capped at
  LEV_CAP 2.0. Example from a live run: 15% / 17.6% = 0.8501x.
- **Rebalance threshold:** trade only when |target L - held L| >= 0.25x.
- **Dial:** TARGET_VOL 0.30 is the setting chosen for testing (2026-11-02). Live config was
  core_config_v3.py, dial 0.30, route CASH-QLD.

## Route (cash account, no margin)
- Above 1.0x: QLD weight = L - 1, QQQ weight = 2 - L.
- Below 1.0x: QQQ plus SGOV for the rest. OUT of trend: all SGOV.
- Backtests use real QLD prices from 2006-06-21. Before that QLD did not exist, so rows use margin above
  1.0x until then (stated, not hidden).
- Margin beats QLD only below a 1.90% borrowing rate; use the QLD route.

## Costs and cash
- Borrowing (margin rows): max(L - 1, 0) x (RF + 0.0075) / 252 per day, RF = 13-week T-bill (Yahoo ^IRX).
- Idle cash earns RF with no spread. Expense ratios are charged.
- Year-count convention matters: an earlier ~12.3% figure came from counting Saturday rows; the reconciled
  figure is 12.14%.

## Fills
- **Realistic (use this):** leverage computed from close[d] is applied from close[d+1] onward (next-day close).
- **Same-close (old published numbers only):** leverage from close[d] credited with the move from close[d]
  to close[d+1]. Impossible live; every older published core figure used it (bug class 19).
- **Near-close variant (live alert):** decide at 15:40 ET, fill at that day's close.
  QQQ_1540[d] = QQQ_adjclose[d] x (NQ_1540[d] / NQ_1600[d]); SMA200 and 60-day vol from real QQQ closes.

## Data
- 100-year series: Ken French daily factors (Mkt-RF + RF). The CSV stored decimals, not percent;
  dividing by 100 again pinned leverage at the cap (a past bug).
- QQQ, QLD, SGOV, ^IRX daily closes (Yahoo). Stooq returned an HTML consent page; CBOE was the cross-check.
- Guard every fetch on row spacing, row count and the price-to-average ratio: a "tidied" Yahoo URL once
  returned ~monthly bars with no error and gave a five-fold under-sized leverage (bug class 20).

## Orders (paper)
- Round share counts, don't floor (flooring once missed the target by 13 points). Search QLD + QQQ share
  combinations for the best fit to the target, then put leftover cash in SGOV. Cash and cap guards;
  no order may ever require borrowing in the cash route.

## Match targets (the rebuild must reproduce these)
Realistic fills (next-day close), QLD route, real QLD from 2006-06-21, margin proxy before:

| Book | 1999+ CAGR / max DD | 2010+ CAGR / max DD |
|---|---|---|
| Buy and hold QQQ | 10.73% / -82.96% | 19.45% / -35.12% |
| Core 0.25 QLD | 11.66% / -32.65% | 17.22% / -32.65% |
| Core 0.30 QLD (GATE 1) | 12.94% / -35.26% (12.9411% / -35.2602%) | 19.15% / -33.37% |

Fill-shift check (TV 0.15, 2006+): same-close 11.26% / -20.11% -> next-day 10.81% / -26.42%.

Older published numbers, **same-close fills** (reproduce them only to confirm the engine, never quote them as live):

| Book | 100-year total US market, 1926-2026 | Since 1999, real QQQ |
|---|---|---|
| Buy and hold | 10.26% / -84.07% | 10.73% / -82.96% |
| Core 0.15 | 12.14% / -33.69% (Sharpe 0.91) | 8.95% / -20.11% |
| Core 0.20 | 13.82% / -40.22% | 10.41% / -26.38% |
| Core 0.25 | 15.21% / -42.36% | 11.47% / -32.24% |
| Core 0.30 | 15.81% / -46.23% | 12.59% / -37.17% |

## Live jobs (Windows Task Scheduler, PC clock Central)
APEX_FWD_premarket 08:00, APEX_FWD_1020 09:20 (10:20 ET), near-close check with phone alert 14:40,
APEX_FWD_close 15:20 (forward runner first, then the daily brief), APEX_FWD_weekly on Sunday.
Phone alerts via ntfy.sh; the topic lives only in the user env var NTFY_TOPIC.

## Past checks worth keeping
- A self-test (22 checks) gated going live: target matches the brief and the forward log, order text
  matches, the >= 1.0x path holds no SGOV, the weight identity holds across 0.00-2.00x.
- Use zlib.crc32, not Python's salted hash(), for any seed derived from a name.

## Not pinned down by the record
- The exact annualization factor for the 60-row vol (most likely sqrt(252)).
- Expense-ratio values used per fund.
- The full forward log and paper books since October 2026 (lost; may be rebuilt from data and must be
  labelled "reconstructed").
