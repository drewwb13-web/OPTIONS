# APEX QUANT — SESSION HANDOVER

> Paste this whole file as your first message to a fresh Claude Code session to
> resume with full context. It assumes you are running in the folder that holds
> the data CSVs: `C:\Users\drew_\OneDrive\Desktop\downloads\`.

## MISSION
Audit and harden an XGBoost ("non-linear brain") that picks high-probability
**structural breakout** trades on ES / SPY options using aggressive order flow,
dealer greeks, and structural levels (POC/VAH/VAL/ORB, Call Wall, Put Wall, King
Node). Top priority: **zero look-ahead / data leakage** ("no hallucinations").
Expect the *honest* win rate to be **lower** than the old V40 number — that drop
is the goal, not a regression.

## DATA FILES (local: `C:\Users\drew_\OneDrive\Desktop\downloads\`)
1. **APEX_FINAL_SQUEEZE_BRAIN.csv** — master training set. 1-min, **Nov 19 2025
   → May 18 2026**. One row per minute. 30 cols:
   `date, time_ct, spx_close, call_vol_ask, call_vol_bid, call_vol_mid,
   put_vol_ask, put_vol_bid, put_vol_mid, call_trades_ask, call_trades_bid,
   put_trades_ask, put_trades_bid, es_open, es_high, es_low, es_close, es_vol,
   POC, VAH, VAL, prev_spx_close, prev_vix_close, Zero_Gamma, Call_Wall,
   Put_Wall, King_Node, Net_GEX, Net_Vanna, Net_Charm`
2. **Ultimate_Advanced_Greek_Tape.csv** — dealer greek exposure. **STRIKE-LEVEL
   (many rows per minute)**, **Jan 2 2026 09:31 → May 6 2026 16:13**. The
   `09:31` start strongly implies **ET**, while the brain uses **CT** `time_ct`.
   40 cols incl. `clean_time, strike, ... net_delta, net_gamma, net_vanna,
   net_charm, net_vomma ..., date_pure`.
3. **OrderFlowUnified.csv** — legacy, superseded by the brain file. Ignore.
4. ***.json** — saved XGBoost models. `Apex_V40_Pure_Breakout.json` is current;
   everything pre-V40 is architecturally incompatible (safe to delete).
5. Mentioned but NOT yet wired in: `ES_Sterilized_Grand_Law_Sterilized.csv`
   (~5yr ES 1-min, needs symbol sterilization), `vix_5_year_day` / `historical`
   (daily OHLC from Yahoo).

## CODE (git branch `claude/compassionate-wright-YGIeC`, repo `drewwb13-web/OPTIONS`)
- **apex_v41_audited.py** — leak-free rebuild of the V40 breakout engine.
- **audit_data.py** — READ-ONLY data sterilization scanner (never writes).
- **.gitignore**

To get them next to the data: clone/pull that branch and copy both `.py` files
into the downloads folder, OR ask this Claude to recreate them from the findings
below.

## BUGS FOUND & FIXED IN apex_v41_audited.py (vs V40)
1. **Cross-day label leak (critical):** the 45-bar win/loss window sliced a
   multi-day array by position, so late-day touches "saw" the next morning.
   FIXED: window clipped to the same trading day.
2. **Threshold off-by-one:** `precision_recall_curve` returns
   `len(thresholds)==len(precisions)-1`; old code mis-indexed (could crash).
   FIXED: aligned before `argmax`.
3. **In-sample threshold:** tuned on the train rows it was fit on.
   FIXED: tuned on a time-separated validation slice (last 15 train days).
4. **Close-only labels (optimistic):** intrabar stop-outs were invisible.
   FIXED: uses `es_high`/`es_low`, assumes STOP first when a bar spans both.
5. **Same-bar entry (1-bar look-ahead):** decided and filled on the same close.
   FIXED: signal on close, FILL on the next bar.
6. **14:15 cutoff dropped held bars:** FIXED — cutoff now gates ENTRIES only;
   trades open at the close are force-flat and counted.
7. **Greek ffill bled across days + silent join failure:** FIXED — ffill within
   each day; prints `greek merge coverage:` as a tripwire.
8. **(From the schemas) Strike-level merge explosion + timezone:** merging the
   strike-level tape multiplied training rows by the strike count; and ET-vs-CT
   misalignment zeroed every greek. FIXED: `drop_duplicates` per minute before
   merge; added `GREEK_TZ_SHIFT_MIN` knob (set to what the audit reports).

## ALREADY CORRECT (do not "fix")
- All model FEATURES are point-in-time (rolling/shift/current-bar only).
- Volume profile (POC/VAH/VAL) uses only the 08:30–09:00 ORB.
- Order-flow delta (calls-at-ask bullish, puts-at-bid bullish) is sane.

## NEXT ACTIONS (in order)
1. **Run the audit:** `python audit_data.py`
   - Read the red flags. Note (a) how many rows-per-minute the greek tape has,
     and (b) which time shift (0 / -60 / +60) gives the best merge coverage.
2. **Set the timezone knob:** open `apex_v41_audited.py`, set
   `GREEK_TZ_SHIFT_MIN = <best shift from the audit>` (likely **-60**).
3. **Train & verify:** `python apex_v41_audited.py`. Confirm the
   `greek merge coverage:` line is now high (not ~0%) and that row counts didn't
   explode.
4. **Implication to confront:** if the timezone bug is real, every V40-and-prior
   brain trained with **all-zero greeks** — it never actually saw dealer
   positioning.
5. **Bigger levers (decide with the user):** walk-forward validation across more
   history; a real **option-P&L / cost model** (an ES level-to-level win is NOT
   a profitable SPY option — theta/IV/spread); fold in the 5-yr ES file. Note
   this pipeline currently trains on **~6 months**, not 5 years.

## CONSTRAINTS
- Keep changes to *correctness*; don't silently alter the trading logic.
- Be explicit about anything you could not verify by running it.
- Develop on branch `claude/compassionate-wright-YGIeC`; commit and push there.
