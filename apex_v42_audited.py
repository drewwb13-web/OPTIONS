# ============================================================================
#  APEX QUANT - V42 AUDITED  (V41 + second-pass leak / realism fixes)
# ============================================================================
#  V41 fixed the label leaks. V42 is a second audit pass. It keeps V41's
#  trading logic (same levels, same features, same labels, same signal rule)
#  and fixes the things that still made the backtest lie or score the wrong
#  thing. apex_v41_audited.py is kept unchanged so you can compare.
#
#  NEW IN V42 (ranked by how much they change the answer):
#    [A] WIN RATE -> REAL P&L. V41 only counted wins/losses. Targets are as
#        close as 3 pts while stops can be much further, so a high win rate
#        can still lose money. V42 records entry/exit price of every trade,
#        subtracts a round-trip cost (COST_PTS), and reports expectancy,
#        profit factor, max drawdown and a t-stat.
#    [B] FILL / LABEL MISMATCH. V41 labels scored the path from bar i+1, but
#        the backtest filled at bar i+1's CLOSE and only checked exits from
#        bar i+2 - so the backtest and the labels measured different trades.
#        V42 fills at bar i+1's OPEN (the first price you can actually get
#        after the signal bar closes) and checks exits on that same bar,
#        exactly matching the labels. Gaps through a stop exit at the open.
#    [C] GREEK REGIME SHIFT. The greek tape starts Jan 2 2026 but training
#        starts Nov 19 2025, so ~1/3 of training rows had greeks = 0 while
#        every test row had real greeks. The model learned two regimes and
#        was tested on only one. V42 trains/tests only on dates the tape
#        covers (REQUIRE_GREEKS).
#    [D] GREEK TIMESTAMP LEAK GUARD. Whether a greek row stamped 08:31 was
#        known at 08:31 or computed from the 08:31-08:32 minute is not
#        documented. V42 lags greeks by GREEK_LAG_BARS (default 1) within the
#        day so they can never be newer than the bar the model is deciding on.
#    [E] GREEK AGGREGATION. V41 assumed net_* are identical across strikes and
#        kept the FIRST strike row. If they are per-strike, that kept one
#        strike's exposure (the lowest strike). V42 checks: constant per
#        minute -> keep one row; varies by strike -> SUM across strikes.
#    [F] SPX WALLS vs ES PRICE. Call_Wall / Put_Wall / King_Node are option
#        strikes (SPX terms) but were compared to es_close with a 4-pt zone.
#        ES trades at a premium to SPX (the futures basis, often 10-60 pts),
#        so "At_Wall" fired in the wrong places and wall targets/stops were
#        off by the basis. V42 shifts them into ES terms using the live
#        (point-in-time, same-day trailing) es_close - spx_close basis.
#    [G] ONE BLIND TEST WINDOW, REUSED. Apr 16-30 has now been looked at by
#        V40 and V41; tuning against it repeatedly makes it in-sample. V42
#        adds an expanding WALK-FORWARD test over every greek-covered day and
#        a "take every signal" BASELINE so you can see if the model adds
#        anything over the raw rule.
#    [H] TIMEZONE TRIPWIRE WAS BLIND. With the wrong timezone ~85% of bars
#        still "match" a greek row (an hour off), so V41's <50% coverage
#        warning never fires. V42 scores the 0 / -60 / +60 min shifts and
#        stops if GREEK_TZ_SHIFT_MIN is not the best one.
#
#  STILL NOT SOLVED (needs data you have not provided):
#    - ES points are not SPY OPTION P&L. Option theta, IV crush and the bid/ask
#      spread are not modeled. A +3 pt ES move in 45 min often does not pay
#      for a short-dated option's spread. Trade ES/MES or add an option model.
#    - How Call_Wall/Put_Wall/King_Node are computed is unknown. If they use
#      open interest or volume from LATER in the same day, that is a leak no
#      code here can detect. Confirm they only use data known at that minute.
#    - ~4 months of greek-covered data is still small. Treat results as a
#      hypothesis, not proof.
#
#  NOTE: Smoke-tested on SYNTHETIC data only (your CSVs are not in the repo).
#  It proves the code runs end to end, NOT that the strategy works.
# ============================================================================

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import precision_recall_curve
import warnings
warnings.filterwarnings('ignore')

print("\n=====================================================================")
print(" [APEX QUANT] V42 AUDITED  (leak-free structural breakout engine) ")
print("=====================================================================")

# =======================================================================
# 0. KNOBS
# =======================================================================
FILE = "APEX_FINAL_SQUEEZE_BRAIN.csv"
GREEK_FILE = "Ultimate_Advanced_Greek_Tape.csv"

# The greek tape starts 09:31 = ET, brain is CT. Run audit_data.py first and
# set this to the shift it reports as best. ET tape vs CT brain -> -60.
GREEK_TZ_SHIFT_MIN = -60     # tape is ET, brain is CT; the [H] check stops the run if this is wrong
GREEK_LAG_BARS = 1          # [D] bars to lag greeks (0 only if you KNOW they are bar-open values)
REQUIRE_GREEKS = True       # [C] only use dates the greek tape covers
WALLS_ARE_SPX = True        # [F] Call_Wall/Put_Wall/King_Node are SPX strikes -> shift to ES
BASIS_SANITY_PTS = 200      # [F] refuse the basis shift if |median basis| is larger than this

COST_PTS = 0.50             # [A] round-trip cost per ES trade, points (fees + 1 tick slip each side)
POINT_VALUE = 50.0          # ES $ per point (MES = 5.0)

TRAIN_END = '2026-04-15'
TEST_START, TEST_END = '2026-04-16', '2026-04-30'
VAL_DAYS = 15               # last N train days used to tune the threshold

WALK_FORWARD = True         # [G]
WF_MIN_TRAIN_DAYS = 30      # days of history before the first walk-forward test block
WF_BLOCK_DAYS = 10          # test block length; model is retrained before each block

# =======================================================================
# 1. LOAD DATABASE & GREEKS
# =======================================================================
greek_cols = ['net_delta', 'net_gamma', 'net_vanna', 'net_charm', 'net_vomma']

try:
    df = pd.read_csv(FILE)
    df.dropna(subset=['spx_close'], inplace=True)
    df['date'] = df['date'].astype(str)
    df = df.sort_values(by=['date', 'time_ct']).reset_index(drop=True)

    df_greeks = pd.read_csv(GREEK_FILE)
    df_greeks['clean_time'] = pd.to_datetime(df_greeks['clean_time'])

    # [H] A WRONG timezone still "matches" most minutes (just an hour off), so
    # a coverage % alone cannot catch it. Score each candidate shift by how
    # many greek minutes land on a brain minute and stop if the knob is wrong.
    brain_keys = set(zip(df['date'], df['time_ct'].astype(str)))
    uniq_t = df_greeks['clean_time'].drop_duplicates()
    def _cov(shift):
        s = uniq_t + pd.Timedelta(minutes=shift)
        keys = zip(s.dt.strftime('%Y-%m-%d'), s.dt.strftime('%H:%M:%S'))
        return sum(k in brain_keys for k in keys) / max(len(uniq_t), 1) * 100
    covs = {sh: _cov(sh) for sh in (0, -60, 60)}
    best_shift = max(covs, key=covs.get)
    print("   greek tz coverage by shift: " + ", ".join(f"{k:+d}m={v:.1f}%" for k, v in covs.items()))
    if covs[best_shift] > covs.get(GREEK_TZ_SHIFT_MIN, _cov(GREEK_TZ_SHIFT_MIN)) + 1:
        print(f"FATAL: GREEK_TZ_SHIFT_MIN={GREEK_TZ_SHIFT_MIN} but {best_shift:+d} aligns better. "
              f"Set GREEK_TZ_SHIFT_MIN = {best_shift} and re-run.")
        raise SystemExit(1)

    # apply the timezone correction BEFORE deriving the join keys
    df_greeks['clean_time'] = df_greeks['clean_time'] + pd.Timedelta(minutes=GREEK_TZ_SHIFT_MIN)
    df_greeks['date'] = df_greeks['clean_time'].dt.strftime('%Y-%m-%d')
    df_greeks['time_ct'] = df_greeks['clean_time'].dt.strftime('%H:%M:%S')

    # [E] Collapse the strike-level tape to ONE row per minute. Columns that
    # are already an aggregate (constant across strikes) keep one value;
    # columns that vary by strike are summed into a total exposure.
    g = df_greeks[['date', 'time_ct'] + greek_cols]
    varies = g.groupby(['date', 'time_ct'])[greek_cols].nunique(dropna=True).max() > 1
    agg = {c: ('sum' if varies[c] else 'first') for c in greek_cols}
    print(f"   greek aggregation per minute: {agg}")
    df_greeks = g.groupby(['date', 'time_ct'], as_index=False).agg(agg)
    greek_dates = set(df_greeks['date'])

    rows_before = len(df)
    df = pd.merge(df, df_greeks, on=['date', 'time_ct'], how='left')
    if len(df) != rows_before:
        print(f"   WARNING: merge changed row count {rows_before} -> {len(df)}.")

    # [C] Drop the days with no greek tape at all, so the model never sees a
    # fake "greeks = 0" regime that never occurs in the test window.
    if REQUIRE_GREEKS:
        n_days = df['date'].nunique()
        df = df[df['date'].isin(greek_dates)].reset_index(drop=True)
        print(f"   REQUIRE_GREEKS: kept {df['date'].nunique()} of {n_days} days "
              f"({df['date'].min()} -> {df['date'].max()})")

    # Surface a silent-join failure: print the match rate.
    match_rate = df[greek_cols[0]].notna().mean() * 100
    print(f"   greek merge coverage: {match_rate:.1f}% of bars matched a greek row")
    if match_rate < 50:
        print("   WARNING: <50% of bars got greeks. Check GREEK_TZ_SHIFT_MIN "
              "(run audit_data.py) before trusting net_* features.")

    # Forward-fill WITHIN each day, then [D] lag within the day so a greek
    # value is only used on bars strictly after its timestamp.
    df[greek_cols] = df.groupby('date')[greek_cols].ffill()
    if GREEK_LAG_BARS > 0:
        df[greek_cols] = df.groupby('date')[greek_cols].shift(GREEK_LAG_BARS)
    df[greek_cols] = df[greek_cols].fillna(0)

except Exception as e:
    print(f"FATAL: {e}")
    raise SystemExit(1)

# =======================================================================
# 2. 30-MINUTE ANCHORS (ORB, POC, VAH, VAL)   [unchanged logic]
# =======================================================================
orb_df = df[(df['time_ct'] >= '08:30:00') & (df['time_ct'] < '09:00:00')].copy()
orb_df['price_tick'] = np.round(orb_df['es_close'] * 4) / 4

profile_records = []
for d, daily_data in orb_df.groupby('date'):
    orb_high = daily_data['es_high'].max()
    orb_low = daily_data['es_low'].min()

    vol_by_price = daily_data.groupby('price_tick')['es_vol'].sum().reset_index()
    if vol_by_price.empty:
        continue

    vol_by_price = vol_by_price.sort_values('es_vol', ascending=False)
    poc = vol_by_price.iloc[0]['price_tick']
    total_vol = vol_by_price['es_vol'].sum()
    target_vol = total_vol * 0.70
    vol_by_price = vol_by_price.sort_values('price_tick').reset_index(drop=True)
    poc_idx = vol_by_price.index[vol_by_price['price_tick'] == poc][0]
    current_vol = vol_by_price.loc[poc_idx, 'es_vol']
    upper_idx, lower_idx = poc_idx, poc_idx

    while current_vol < target_vol and (upper_idx < len(vol_by_price) - 1 or lower_idx > 0):
        vol_up = vol_by_price.loc[upper_idx + 1, 'es_vol'] if upper_idx < len(vol_by_price) - 1 else 0
        vol_down = vol_by_price.loc[lower_idx - 1, 'es_vol'] if lower_idx > 0 else 0
        if vol_up >= vol_down and upper_idx < len(vol_by_price) - 1:
            upper_idx += 1; current_vol += vol_up
        elif lower_idx > 0:
            lower_idx -= 1; current_vol += vol_down

    vah = vol_by_price.loc[upper_idx, 'price_tick']
    val = vol_by_price.loc[lower_idx, 'price_tick']
    profile_records.append({'date': d, 'ORB_High': orb_high, 'ORB_Low': orb_low,
                            'Anchored_POC': poc, 'Anchored_VAH': vah, 'Anchored_VAL': val})

if 'POC' in df.columns:
    df.drop(columns=['POC', 'VAH', 'VAL'], inplace=True, errors='ignore')
df = df.merge(pd.DataFrame(profile_records), on='date', how='inner')

# [F] Put the option-strike levels into ES terms. Basis = es_close - spx_close,
# smoothed with a trailing same-day median (point-in-time, no future bars).
basis = (df['es_close'] - df['spx_close']).groupby(df['date']).transform(
    lambda x: x.rolling(15, min_periods=1).median())
med_basis = float(basis.median())
print(f"   ES-SPX basis: median {med_basis:+.2f} pts "
      f"(min {basis.min():+.2f}, max {basis.max():+.2f})")
if WALLS_ARE_SPX:
    if abs(med_basis) > BASIS_SANITY_PTS:
        print(f"   WARNING: |basis| > {BASIS_SANITY_PTS} pts - spx_close is probably not SPX "
              "(SPY x10? stale?). Walls left UNSHIFTED. Fix spx_close first.")
    else:
        for c in ['Call_Wall', 'Put_Wall', 'King_Node']:
            df[c] = df[c] + basis
        print("   walls shifted into ES terms (Call_Wall, Put_Wall, King_Node)")

df = df[df['time_ct'] >= '09:00:00'].copy()
df.dropna(subset=['Call_Wall', 'ORB_High'], inplace=True)
df.reset_index(drop=True, inplace=True)

# =======================================================================
# 3. STRUCTURAL TOUCHES (THE BREAK ZONES)   [unchanged logic]
# =======================================================================
ZONE = 4.0

df['At_Level'] = np.where(
    (df['es_close'].sub(df['Anchored_VAH']).abs() <= ZONE) |
    (df['es_close'].sub(df['Anchored_VAL']).abs() <= ZONE) |
    (df['es_close'].sub(df['ORB_High']).abs() <= ZONE) |
    (df['es_close'].sub(df['ORB_Low']).abs() <= ZONE),
    1, 0
)
df['At_Wall'] = np.where(
    (df['es_close'].sub(df['Call_Wall']).abs() <= ZONE) |
    (df['es_close'].sub(df['Put_Wall']).abs() <= ZONE),
    1, 0
)
df['At_King'] = np.where((df['es_close'] - df['King_Node']).abs() <= ZONE, 1, 0)
df['At_POC'] = np.where((df['es_close'] - df['Anchored_POC']).abs() <= ZONE, 1, 0)
df['At_Structure'] = np.where(df['At_Level'] | df['At_Wall'] | df['At_King'] | df['At_POC'], 1, 0)

# =======================================================================
# 4. ORDER FLOW ROUTING   [unchanged logic - all point-in-time]
# =======================================================================
df['Active_Vol_Delta'] = df['call_vol_ask'] - df['call_vol_bid'] + df['put_vol_bid'] - df['put_vol_ask']
df['Active_Trade_Delta'] = df['call_trades_ask'] - df['call_trades_bid'] + df['put_trades_bid'] - df['put_trades_ask']
df['Active_Mid_Vol'] = df['call_vol_mid'] + df['put_vol_mid']

df['Cumulative_3Min_Delta'] = df.groupby('date')['Active_Vol_Delta'].transform(lambda x: x.rolling(3).sum().fillna(0))
df['Delta_Momentum'] = df['Active_Vol_Delta'] - df.groupby('date')['Active_Vol_Delta'].shift(1).fillna(0)
df['Price_Velocity_3Min'] = df.groupby('date')['es_close'].transform(lambda x: x - x.shift(3).fillna(x))

features_to_shift = []
for i in range(1, 4):
    df[f'Vol_Delta_T{i}'] = df.groupby('date')['Active_Vol_Delta'].shift(i).fillna(0)
    features_to_shift.append(f'Vol_Delta_T{i}')

df = df[df['time_ct'] >= '09:15:00'].copy().reset_index(drop=True)

# =======================================================================
# 5. DYNAMIC TARGET MATRIX + LEAK-FREE WALK-FORWARD LABELS  [V41 logic]
# =======================================================================
levels = ['Anchored_POC', 'Anchored_VAH', 'Anchored_VAL', 'ORB_High', 'ORB_Low', 'Call_Wall', 'Put_Wall']

# Gate ENTRIES (not holds) at 14:15. Every bar stays in df so an open trade
# can be managed to the close.
ENTRY_CUTOFF = "14:15:00"
df['Entry_Eligible'] = (df['At_Structure'] == 1) & (df['time_ct'] < ENTRY_CUTOFF)

def calculate_dynamic_risk_matrix(row):
    current_price = row['es_close']
    valid_up = [row[c] for c in levels if pd.notna(row[c]) and row[c] >= current_price + 3.0]
    valid_down = [row[c] for c in levels if pd.notna(row[c]) and row[c] <= current_price - 3.0]
    target_up = min(valid_up) if valid_up else current_price + 10.0
    target_down = max(valid_down) if valid_down else current_price - 10.0
    valid_stop_up = [row[c] for c in levels if pd.notna(row[c]) and row[c] <= current_price - 2.0]
    valid_stop_down = [row[c] for c in levels if pd.notna(row[c]) and row[c] >= current_price + 2.0]
    stop_up = max(valid_stop_up) if valid_stop_up else current_price - 6.0
    stop_down = min(valid_stop_down) if valid_stop_down else current_price + 6.0
    return pd.Series([target_up, target_down, stop_up, stop_down])

df[['Target_Up', 'Target_Down', 'Stop_Up', 'Stop_Down']] = np.nan
elig_mask = df['Entry_Eligible'].values
if elig_mask.any():
    mat = df.loc[elig_mask].apply(calculate_dynamic_risk_matrix, axis=1)
    df.loc[elig_mask, ['Target_Up', 'Target_Down', 'Stop_Up', 'Stop_Down']] = mat.values

# ---- Precompute SAME-DAY positional boundaries -----------------------------
date_arr = df['date'].values
n = len(df)
day_end_pos = np.empty(n, dtype=np.int64)
start = 0
for k in range(1, n + 1):
    if k == n or date_arr[k] != date_arr[start]:
        day_end_pos[start:k] = k - 1
        start = k

es_high_arr = df['es_high'].values
es_low_arr = df['es_low'].values
target_up_arr = df['Target_Up'].values
stop_up_arr = df['Stop_Up'].values
target_down_arr = df['Target_Down'].values
stop_down_arr = df['Stop_Down'].values
elig_arr = df['Entry_Eligible'].values

HOLD_BARS = 45
bull_targets = np.zeros(n, dtype=np.int8)
bear_targets = np.zeros(n, dtype=np.int8)

# Path from bar i+1 (the fill bar) for HOLD_BARS bars, same day only, scored
# intrabar with high/low; a bar spanning both levels counts as the STOP.
for i in range(n):
    if not elig_arr[i]:
        continue

    end = min(i + 1 + HOLD_BARS, day_end_pos[i] + 1)
    if end <= i + 1:
        continue
    hi = es_high_arr[i + 1:end]
    lo = es_low_arr[i + 1:end]

    t_up, s_up = target_up_arr[i], stop_up_arr[i]
    if not (np.isnan(t_up) or np.isnan(s_up)):
        for b in range(len(hi)):
            if lo[b] <= s_up:
                break
            if hi[b] >= t_up:
                bull_targets[i] = 1
                break

    t_dn, s_dn = target_down_arr[i], stop_down_arr[i]
    if not (np.isnan(t_dn) or np.isnan(s_dn)):
        for b in range(len(hi)):
            if hi[b] >= s_dn:
                break
            if lo[b] <= t_dn:
                bear_targets[i] = 1
                break

df['bull_breakout'] = bull_targets
df['bear_breakout'] = bear_targets

df['TARGET_BREAKOUT'] = np.where(
    ((df['Active_Vol_Delta'] > 0) & (df['bull_breakout'] == 1)) |
    ((df['Active_Vol_Delta'] < 0) & (df['bear_breakout'] == 1)),
    1, 0
)

df_breaks = df[df['Entry_Eligible']].copy()

# =======================================================================
# 6. TRAIN / THRESHOLD / BACKTEST HELPERS
# =======================================================================
ml_features = [
    'At_Level', 'At_Wall', 'At_King', 'At_POC',
    'Active_Vol_Delta', 'Active_Trade_Delta', 'Active_Mid_Vol',
    'Cumulative_3Min_Delta', 'Delta_Momentum', 'Price_Velocity_3Min',
    'net_delta', 'net_gamma', 'net_vanna', 'net_charm', 'net_vomma'
] + features_to_shift


def train_model(train_dates, val_dates, verbose=True):
    """Fit on train_dates, tune the threshold on the later val_dates."""
    df_train = df_breaks[df_breaks['date'].isin(train_dates)]
    df_val = df_breaks[df_breaks['date'].isin(val_dates)]
    if df_train.empty or df_train['TARGET_BREAKOUT'].nunique() < 2:
        return None, None, None

    X_train, y_train = df_train[ml_features], df_train['TARGET_BREAKOUT']
    pos_n = int((y_train == 1).sum())
    neg_n = int((y_train == 0).sum())
    pos_weight = neg_n / pos_n if pos_n > 0 else 1.0
    if verbose:
        print(f"   train rows={len(df_train)}  (pos={pos_n}, neg={neg_n}, "
              f"base rate={pos_n/len(df_train)*100:.1f}%)   val rows={len(df_val)}")

    model = xgb.XGBClassifier(
        objective='binary:logistic', n_estimators=550, learning_rate=0.02,
        max_depth=5, subsample=0.8, colsample_bytree=0.8, scale_pos_weight=pos_weight,
        random_state=42, eval_metric='auc', verbosity=0
    )
    model.fit(X_train, y_train)

    if not df_val.empty and df_val['TARGET_BREAKOUT'].nunique() == 2:
        thr_X, thr_y, thr_src = df_val[ml_features], df_val['TARGET_BREAKOUT'], "validation"
    else:
        thr_X, thr_y, thr_src = X_train, y_train, "train (no usable validation slice)"

    calib_probs = model.predict_proba(thr_X)[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(thr_y, calib_probs)
    p, r = precisions[:-1], recalls[:-1]
    f1_scores = 2 * (p * r) / (p + r + 1e-9)
    opt_thresh = float(thresholds[int(np.argmax(f1_scores))])
    opt_thresh = min(max(opt_thresh, 0.40), 0.60)
    return model, opt_thresh, thr_src


def run_backtest(model, thresh, dates):
    """One trade at a time per day. Signal on bar close, FILL at the NEXT bar's
    OPEN [B], exits checked from the fill bar on (stop first), HOLD_BARS time
    exit, flat at the last bar of the day. Returns a list of trade dicts."""
    dates = sorted(dates)
    sub = df[df['date'].isin(dates)]
    elig = sub[sub['Entry_Eligible']]
    prob_map = {}
    if not elig.empty:
        if model is None:                       # baseline: take every signal
            prob_map = dict.fromkeys(elig.index, 1.0)
        else:
            prob_map = dict(zip(elig.index, model.predict_proba(elig[ml_features])[:, 1]))

    trades = []
    for d, day in sub.groupby('date', sort=True):
        idxs = day.index.values
        t_arr = day['time_ct'].values
        o, h, l, c = (day[k].values for k in ['es_open', 'es_high', 'es_low', 'es_close'])
        dlt_arr = day['Active_Vol_Delta'].values
        vel_arr = day['Price_Velocity_3Min'].values
        tu, su = day['Target_Up'].values, day['Stop_Up'].values
        td, sd = day['Target_Down'].values, day['Stop_Down'].values
        last = len(day) - 1

        in_trade, pending = False, 0
        pend_t = pend_s = 0.0
        direction, target, stop, entry, entry_time, bars = 0, 0.0, 0.0, 0.0, '', 0

        for k in range(len(day)):
            # 1) Fill a signal armed on the previous bar at THIS bar's open.
            if pending != 0:
                in_trade, direction = True, pending
                target, stop = pend_t, pend_s
                entry, entry_time, bars = o[k], t_arr[k], 0
                pending = 0

            # 2) Manage the open trade (the fill bar included, stop first).
            if in_trade:
                bars += 1
                exit_px, tag = None, ''
                if direction == 1:
                    if l[k] <= stop:
                        exit_px, tag = min(o[k], stop), 'STOP'
                    elif h[k] >= target:
                        exit_px, tag = max(o[k], target), 'TARGET'
                else:
                    if h[k] >= stop:
                        exit_px, tag = max(o[k], stop), 'STOP'
                    elif l[k] <= target:
                        exit_px, tag = min(o[k], target), 'TARGET'
                if exit_px is None and bars >= HOLD_BARS:
                    exit_px, tag = c[k], 'TIME'
                if exit_px is None and k == last:
                    exit_px, tag = c[k], 'EOD'
                if exit_px is not None:
                    gross = (exit_px - entry) * direction
                    trades.append({'date': d, 'entry_time': entry_time, 'exit_time': t_arr[k],
                                   'dir': 'LONG' if direction == 1 else 'SHORT',
                                   'entry': entry, 'exit': exit_px, 'outcome': tag,
                                   'gross_pts': gross, 'net_pts': gross - COST_PTS})
                    in_trade = False
                continue

            # 3) Look for a fresh signal at an eligible structure bar.
            prob = prob_map.get(idxs[k])
            if prob is not None and prob >= thresh and k < last:
                dlt, vel = dlt_arr[k], vel_arr[k]
                if dlt > 0 and vel >= -0.5:
                    pending = 1; pend_t, pend_s = tu[k], su[k]
                elif dlt < 0 and vel <= 0.5:
                    pending = -1; pend_t, pend_s = td[k], sd[k]
    return trades


def summarize(trades, title):
    print(f"\n   --- {title} ---")
    if not trades:
        print("   no trades")
        return
    t = pd.DataFrame(trades)
    net = t['net_pts']
    wins, losses = net[net > 0], net[net <= 0]
    gross_loss = -losses.sum()
    pf = wins.sum() / gross_loss if gross_loss > 0 else float('inf')
    equity = net.cumsum()
    max_dd = float((equity - equity.cummax()).min())
    tstat = net.mean() / (net.std(ddof=1) / np.sqrt(len(net))) if len(net) > 1 and net.std(ddof=1) > 0 else 0.0
    counts = t['outcome'].value_counts().to_dict()
    print(f"   trades={len(t)}  days={t['date'].nunique()}  outcomes={counts}")
    print(f"   win rate (net>0)={len(wins)/len(t)*100:.1f}%   "
          f"avg win={wins.mean() if len(wins) else 0:+.2f}  avg loss={losses.mean() if len(losses) else 0:+.2f} pts")
    print(f"   expectancy={net.mean():+.3f} pts/trade (after {COST_PTS} cost)   "
          f"total={net.sum():+.2f} pts = ${net.sum()*POINT_VALUE:+,.0f}/contract")
    print(f"   profit factor={pf:.2f}   max drawdown={max_dd:.2f} pts   t-stat={tstat:+.2f}"
          f"   {'(|t|<2: not distinguishable from luck)' if abs(tstat) < 2 else ''}")


# =======================================================================
# 7. FIXED SPLIT (same windows as V41, for comparison)
# =======================================================================
all_dates = sorted(df_breaks['date'].unique())
train_pool = [d for d in all_dates if d <= TRAIN_END]
val_dates = train_pool[-VAL_DAYS:] if len(train_pool) > VAL_DAYS + 5 else []
train_dates = [d for d in train_pool if d not in set(val_dates)]
test_dates = [d for d in all_dates if TEST_START <= d <= TEST_END]

print("\n=====================================================================")
print(f" FIXED SPLIT: train <= {TRAIN_END}, test {TEST_START} -> {TEST_END}")
print("=====================================================================")
model, opt_thresh, thr_src = train_model(train_dates, val_dates)
if model is None:
    print("FATAL: training set is empty or single-class. Check dates / data.")
    raise SystemExit(1)
print(f"   breakout threshold = {opt_thresh*100:.2f}%  (tuned on {thr_src})")
print("   NOTE: V40/V41 were already judged on this window, so it is no longer truly blind.")

fixed_trades = run_backtest(model, opt_thresh, test_dates)
summarize(fixed_trades, "MODEL, fixed test window")
summarize(run_backtest(None, 0.0, test_dates), "BASELINE (every signal, no model), fixed test window")

if fixed_trades:
    print(f"\n   {'DATE':<12} | {'TRADES':<6} | {'WINS':<5} | {'NET PTS':>8}")
    for d, g in pd.DataFrame(fixed_trades).groupby('date'):
        print(f"   {d:<12} | {len(g):<6} | {int((g['net_pts'] > 0).sum()):<5} | {g['net_pts'].sum():>+8.2f}")

print("\nFEATURE IMPORTANCE (top 15)")
feature_rank = pd.DataFrame({'Feature': ml_features, 'Importance': model.feature_importances_})
feature_rank = feature_rank.sort_values(by='Importance', ascending=False).head(15)
feature_rank['Importance'] = (feature_rank['Importance'] * 100).map('{:.2f}%'.format)
print(feature_rank.to_string(index=False))

print("\n=====================================================================")
print(f" TRADE AUDIT: first test day ({test_dates[0] if test_dates else TEST_START})")
print("=====================================================================")
day1 = [t for t in fixed_trades if test_dates and t['date'] == test_dates[0]]
for t in day1:
    print(f"{t['entry_time']} -> {t['exit_time']} | {t['dir']:<5} | {t['outcome']:<6} | "
          f"{t['entry']:.2f} -> {t['exit']:.2f} | net {t['net_pts']:+.2f}")
if not day1:
    print("No trades triggered.")

model.save_model("Apex_V42_Audited.json")
print("\nsaved model -> Apex_V42_Audited.json")

# =======================================================================
# 8. EXPANDING WALK-FORWARD  [G]  - the result to actually trust
# =======================================================================
if WALK_FORWARD:
    print("\n=====================================================================")
    print(f" WALK-FORWARD: retrain every {WF_BLOCK_DAYS} days on all prior days "
          f"(last {VAL_DAYS} as validation)")
    print("=====================================================================")
    wf_trades, base_trades = [], []
    for b in range(WF_MIN_TRAIN_DAYS, len(all_dates), WF_BLOCK_DAYS):
        block = all_dates[b:b + WF_BLOCK_DAYS]
        hist = all_dates[:b]
        v = hist[-VAL_DAYS:] if len(hist) > VAL_DAYS + 5 else []
        tr = hist[:len(hist) - len(v)]
        m, th, _ = train_model(tr, v, verbose=False)
        if m is None:
            continue
        ft = run_backtest(m, th, block)
        bt = run_backtest(None, 0.0, block)
        wf_trades += ft
        base_trades += bt
        net = sum(t['net_pts'] for t in ft)
        print(f"   {block[0]} -> {block[-1]} | thr={th*100:5.1f}% | trades={len(ft):<4} | net={net:+8.2f} pts")
    summarize(wf_trades, "MODEL, walk-forward (all out-of-sample)")
    summarize(base_trades, "BASELINE (every signal, no model), same days")
print("=====================================================================\n")
