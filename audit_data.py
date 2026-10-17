# ============================================================================
#  APEX DATA AUDIT  -  read-only sterilization scanner
# ============================================================================
#  Reads your two source CSVs and reports the data-integrity problems that
#  silently corrupt the model. It NEVER writes to or modifies your files.
#
#  Run it:
#     python audit_data.py
#  or point it at a different folder:
#     python audit_data.py "C:\Users\drew_\OneDrive\Desktop\downloads"
#
#  What it checks (the stuff that makes a backtest lie):
#    1.  Brain file: shape, date range, duplicate (date,time_ct) rows
#    2.  Brain file: NaN audit on the columns the model actually uses
#    3.  Brain file: OHLC sanity (high>=low, high>=open/close, etc.)
#    4.  Brain file: per-day minute continuity (gaps / missing bars)
#    5.  Brain file: wall sanity (Call_Wall vs Put_Wall ordering)
#    6.  Greek tape: STRIKE-LEVEL explosion (rows per minute) -> the merge bug
#    7.  TIMEZONE alignment: ET-vs-CT mismatch between the two files
#    8.  Merge simulation: coverage % and row-explosion factor of the V40 join
# ============================================================================

import os
import sys
import pandas as pd
import numpy as np

DOWNLOADS = r"C:\Users\drew_\OneDrive\Desktop\downloads"
if len(sys.argv) > 1:
    DOWNLOADS = sys.argv[1]

BRAIN = os.path.join(DOWNLOADS, "APEX_FINAL_SQUEEZE_BRAIN.csv")
GREEK = os.path.join(DOWNLOADS, "Ultimate_Advanced_Greek_Tape.csv")

red_flags = []


def hr(title):
    print("\n" + "=" * 70)
    print(" " + title)
    print("=" * 70)


def flag(msg):
    red_flags.append(msg)
    print("  [RED FLAG] " + msg)


# ----------------------------------------------------------------------------
# BRAIN FILE
# ----------------------------------------------------------------------------
hr("1-5.  BRAIN FILE  (APEX_FINAL_SQUEEZE_BRAIN.csv)")
if not os.path.exists(BRAIN):
    flag(f"Brain file not found at {BRAIN}")
    brain = None
else:
    brain = pd.read_csv(BRAIN)
    print(f"  rows={len(brain):,}   cols={len(brain.columns)}")
    print(f"  columns: {list(brain.columns)}")

    if 'date' in brain.columns:
        print(f"  date range: {brain['date'].min()}  ->  {brain['date'].max()}")
        print(f"  trading days: {brain['date'].nunique()}")

    # 1. duplicate timestamps
    if {'date', 'time_ct'}.issubset(brain.columns):
        dup = brain.duplicated(subset=['date', 'time_ct']).sum()
        if dup:
            flag(f"{dup:,} duplicate (date,time_ct) rows in the brain file "
                 "-> these multiply your training rows. Expect exactly one per minute.")
        else:
            print("  OK: one row per (date,time_ct), no duplicates.")

    # 2. NaN audit on the columns the model consumes
    used = ['spx_close', 'es_open', 'es_high', 'es_low', 'es_close', 'es_vol',
            'call_vol_ask', 'call_vol_bid', 'put_vol_ask', 'put_vol_bid',
            'call_vol_mid', 'put_vol_mid', 'call_trades_ask', 'call_trades_bid',
            'put_trades_ask', 'put_trades_bid',
            'Call_Wall', 'Put_Wall', 'King_Node', 'POC', 'VAH', 'VAL']
    present = [c for c in used if c in brain.columns]
    missing = [c for c in used if c not in brain.columns]
    if missing:
        flag(f"columns the code expects but the file lacks: {missing}")
    nans = brain[present].isna().sum()
    nans = nans[nans > 0]
    if len(nans):
        print("  NaN counts (model-used columns):")
        for c, v in nans.items():
            pct = v / len(brain) * 100
            print(f"     {c:<18} {v:>8,}  ({pct:5.1f}%)")
            if pct > 5:
                flag(f"'{c}' is {pct:.1f}% NaN -> rows get dropped or features go stale.")
    else:
        print("  OK: no NaNs in model-used columns.")

    # 3. OHLC sanity
    if {'es_open', 'es_high', 'es_low', 'es_close'}.issubset(brain.columns):
        o, h, l, c = brain['es_open'], brain['es_high'], brain['es_low'], brain['es_close']
        bad = ((h < l) | (h < o) | (h < c) | (l > o) | (l > c)).sum()
        if bad:
            flag(f"{bad:,} bars violate OHLC logic (high<low, high<close, etc.) "
                 "-> corrupt candles poison the intrabar win/loss labels.")
        else:
            print("  OK: OHLC relationships are valid on every bar.")
        if (brain['es_vol'] <= 0).sum() if 'es_vol' in brain.columns else 0:
            flag("some bars have es_vol <= 0 -> volume profile (POC/VAH/VAL) may be skewed.")

    # 4. per-day minute continuity
    if {'date', 'time_ct'}.issubset(brain.columns):
        sample_day = brain['date'].dropna().unique()
        gaps_total = 0
        for d in sample_day:
            t = pd.to_datetime(brain.loc[brain['date'] == d, 'time_ct'], format='%H:%M:%S', errors='coerce').dropna()
            t = t.sort_values()
            rth = t[(t.dt.time >= pd.to_datetime('08:30').time()) & (t.dt.time <= pd.to_datetime('15:00').time())]
            if len(rth) > 1:
                deltas = rth.diff().dropna().dt.total_seconds() / 60.0
                gaps_total += int((deltas > 1).sum())
        if gaps_total:
            print(f"  NOTE: {gaps_total} intraday minute-gaps across all days "
                  "(holidays/half-days are normal; large counts mean missing tape).")
        else:
            print("  OK: no intraday minute gaps detected in RTH.")

    # 5. wall sanity
    if {'Call_Wall', 'Put_Wall'}.issubset(brain.columns):
        inverted = (brain['Call_Wall'] < brain['Put_Wall']).sum()
        if inverted:
            flag(f"{inverted:,} bars have Call_Wall < Put_Wall (walls inverted) "
                 "-> check the GEX sign / strike mapping in your wall calc.")
        else:
            print("  OK: Call_Wall >= Put_Wall on every bar.")


# ----------------------------------------------------------------------------
# GREEK TAPE  -  strike-level explosion + timezone
# ----------------------------------------------------------------------------
hr("6-8.  GREEK TAPE  (Ultimate_Advanced_Greek_Tape.csv)")
if not os.path.exists(GREEK):
    flag(f"Greek tape not found at {GREEK}")
else:
    # read only what we need (this file is strike-level and can be millions of rows)
    cols_want = ['clean_time', 'strike', 'net_delta', 'net_gamma']
    try:
        head = pd.read_csv(GREEK, nrows=5)
        usecols = [c for c in cols_want if c in head.columns]
    except Exception as e:
        usecols = None
        print(f"  could not preview header: {e}")
    g = pd.read_csv(GREEK, usecols=usecols) if usecols else pd.read_csv(GREEK)
    print(f"  rows={len(g):,}   (loaded columns: {list(g.columns)})")

    if 'clean_time' in g.columns:
        g['clean_time'] = pd.to_datetime(g['clean_time'], errors='coerce')
        g = g.dropna(subset=['clean_time'])
        g['gdate'] = g['clean_time'].dt.strftime('%Y-%m-%d')
        g['gtime'] = g['clean_time'].dt.strftime('%H:%M:%S')

        print(f"  time range: {g['clean_time'].min()}  ->  {g['clean_time'].max()}")
        print(f"  intraday hour span: {g['clean_time'].dt.strftime('%H:%M').min()} "
              f"-> {g['clean_time'].dt.strftime('%H:%M').max()}")

        # 6. STRIKE-LEVEL EXPLOSION
        per_min = g.groupby(['gdate', 'gtime']).size()
        median_strikes = int(per_min.median())
        if median_strikes > 1:
            flag(f"Greek tape has ~{median_strikes} rows PER MINUTE (strike-level). "
                 "Merging it as-is multiplies your training rows by that factor. "
                 "FIX: drop_duplicates(['date','time_ct']) BEFORE merging.")
        else:
            print("  OK: greek tape is already one row per minute.")

        # 7. TIMEZONE alignment vs the brain file
        if brain is not None and {'date', 'time_ct'}.issubset(brain.columns):
            brain_keys = set(zip(brain['date'].astype(str), brain['time_ct'].astype(str)))
            g_dedup = g.drop_duplicates(subset=['gdate', 'gtime'])

            def coverage(shift_min):
                shifted = (g_dedup['clean_time'] + pd.Timedelta(minutes=shift_min))
                keys = set(zip(shifted.dt.strftime('%Y-%m-%d'), shifted.dt.strftime('%H:%M:%S')))
                inter = len(keys & brain_keys)
                return inter / max(len(keys), 1) * 100

            cov0 = coverage(0)
            cov_minus60 = coverage(-60)   # greek is ET, brain is CT (ET = CT + 1h) -> subtract 60
            cov_plus60 = coverage(+60)
            print(f"  merge coverage at  0h shift: {cov0:5.1f}%")
            print(f"  merge coverage at -1h shift: {cov_minus60:5.1f}%   (greek=ET, brain=CT)")
            print(f"  merge coverage at +1h shift: {cov_plus60:5.1f}%")
            best = max([(cov0, 0), (cov_minus60, -60), (cov_plus60, 60)])
            if best[1] != 0:
                flag(f"Best alignment is at a {best[1]} min shift ({best[0]:.0f}%), NOT 0h. "
                     "Your two files are in DIFFERENT TIMEZONES. The V40 merge on raw "
                     "time strings still matches most minutes, but every greek is attached "
                     "to the WRONG bar (an hour off) - a silent misalignment, not zeros. "
                     f"FIX: convert clean_time by {best[1]} min before merging.")
            elif cov0 < 60:
                flag(f"Even at 0h the join only covers {cov0:.0f}% of greek minutes. "
                     "Investigate seconds rounding / missing minutes before trusting greeks.")
            else:
                print(f"  OK: timezones align at 0h ({cov0:.0f}% coverage).")

            # 8. merge explosion simulation (what V40 actually does)
            naive = g[['gdate', 'gtime']].rename(columns={'gdate': 'date', 'gtime': 'time_ct'})
            naive['date'] = naive['date'].astype(str)
            merged_rows = brain.merge(naive, on=['date', 'time_ct'], how='left')
            factor = len(merged_rows) / len(brain)
            print(f"  V40-style merge would turn {len(brain):,} brain rows into "
                  f"{len(merged_rows):,} rows  (x{factor:.2f}).")
            if factor > 1.01:
                flag(f"The raw V40 merge inflates the brain file x{factor:.2f} via strike "
                     "duplication. apex_v41_audited.py must dedup the greek tape first.")


# ----------------------------------------------------------------------------
hr("SUMMARY")
if red_flags:
    print(f"  {len(red_flags)} red flag(s) found:\n")
    for i, f in enumerate(red_flags, 1):
        print(f"  {i}. {f}")
    print("\n  -> Fix these in the data-assembly step BEFORE retraining. A clean")
    print("     model on a dirty join is still garbage.")
else:
    print("  No red flags. Data joins look sterilized; proceed to modeling.")
print("=" * 70)
