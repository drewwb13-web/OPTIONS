# ============================================================================
#  APEX QUANT - V41 AUDITED  (leak-free rebuild of the V40 breakout engine)
# ============================================================================
#  This is a CORRECTNESS audit of your V40 script. Every change vs your
#  original is listed here so nothing is hidden ("no hallucinations").
#
#  ALREADY CORRECT IN YOUR CODE (left as-is):
#    - All MODEL FEATURES are point-in-time (rolling / shift / current-bar
#      only). No feature looks into the future. Feature engineering is clean.
#    - The volume-profile / value-area (POC/VAH/VAL) uses only the 08:30-09:00
#      opening range, so it is fully known before you trade it.
#    - The order-flow delta construction (calls-at-ask bullish, etc.) is sane.
#
#  BUGS FIXED (ranked by how badly they inflate / corrupt results):
#    [1] CROSS-DAY LABEL LEAK (critical). The label loop sliced
#        es_close_arr[i+1 : i+46] by POSITION over a multi-day array, so a
#        late-day touch "saw" the NEXT morning's prices (overnight gap
#        included) when scoring win/loss. FIX: the 45-bar forward window is
#        now clipped to the SAME trading day.
#    [2] THRESHOLD OFF-BY-ONE. precision_recall_curve returns
#        len(thresholds) == len(precisions) - 1. Indexing thresholds[argmax(f1)]
#        over the full-length f1 is misaligned (and IndexError at the last
#        point). FIX: align f1 to thresholds before argmax.
#    [3] IN-SAMPLE THRESHOLD. The decision threshold was tuned on the same
#        rows the model was fit on. FIX: tune on a time-separated validation
#        slice (the last 15 training days), never on the test set.
#    [4] CLOSE-ONLY LABELS (optimistic). Win/loss used es_close only, so
#        intrabar stop-outs were invisible. FIX: use es_high/es_low and, when
#        a single bar spans both target and stop, assume the STOP hit first.
#    [5] SAME-BAR ENTRY (1-bar look-ahead). The test decided AND filled on the
#        same bar's close. FIX: signal on bar close, FILL on the next bar.
#    [6] EOD HANDLING. The 14:15 cutoff now gates ENTRIES only; trades already
#        open may run to the close, and anything still open at the last bar is
#        force-flat and counted. This keeps labels and execution consistent.
#    [7] GREEK FFILL bled across days; now forward-filled WITHIN each day.
#        Added a merge-coverage print so a bad timestamp join can't fail
#        silently into all-zero greeks.
#
#  NOT FIXED HERE (needs inputs you have not provided - see chat):
#    - No transaction cost / option spread / theta. An ES level-to-level "win"
#      is NOT a profitable SPY option. Add a cost model before trusting P&L.
#    - 2-week test window is too short to conclude anything. Use walk-forward.
#
#  NOTE: This file was NOT executed (your CSVs are not in the repo). Column
#  names are kept identical to your V40 so it should run unchanged on your
#  data; verify the merge-coverage line prints a high % before trusting output.
# ============================================================================

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import precision_recall_curve
import warnings
warnings.filterwarnings('ignore')

print("\n=====================================================================")
print(" [APEX QUANT] V41 AUDITED  (leak-free structural breakout engine) ")
print("=====================================================================")

# =======================================================================
# 1. LOAD DATABASE & GREEKS
# =======================================================================
FILE = "APEX_FINAL_SQUEEZE_BRAIN.csv"
GREEK_FILE = "Ultimate_Advanced_Greek_Tape.csv"

try:
    df = pd.read_csv(FILE)
    df.dropna(subset=['spx_close'], inplace=True)
    df = df.sort_values(by=['date', 'time_ct']).reset_index(drop=True)

    df_greeks = pd.read_csv(GREEK_FILE)
    df_greeks['clean_time'] = pd.to_datetime(df_greeks['clean_time'])
    df_greeks['date'] = df_greeks['clean_time'].dt.strftime('%Y-%m-%d')
    df_greeks['time_ct'] = df_greeks['clean_time'].dt.strftime('%H:%M:%S')

    greek_cols = ['net_delta', 'net_gamma', 'net_vanna', 'net_charm', 'net_vomma']
    df_greeks = df_greeks[['date', 'time_ct'] + greek_cols].copy()

    df = pd.merge(df, df_greeks, on=['date', 'time_ct'], how='left')

    # [FIX 7] Surface a silent-join failure: if timestamps don't line up the
    # greeks become all-zero and you'd never know. Print the match rate.
    match_rate = df[greek_cols[0]].notna().mean() * 100
    print(f"   greek merge coverage: {match_rate:.1f}% of bars matched a greek row")
    if match_rate < 50:
        print("   WARNING: <50% of bars got greeks. Check clean_time vs time_ct "
              "(timezone / seconds rounding) before trusting net_* features.")

    # [FIX 7] Forward-fill greeks WITHIN each day so values don't bleed across
    # the overnight gap; anything still missing -> 0.
    df[greek_cols] = df.groupby('date')[greek_cols].ffill()
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
# 5. DYNAMIC TARGET MATRIX + LEAK-FREE WALK-FORWARD LABELS
# =======================================================================
levels = ['Anchored_POC', 'Anchored_VAH', 'Anchored_VAL', 'ORB_High', 'ORB_Low', 'Call_Wall', 'Put_Wall']

# [FIX 6] Gate ENTRIES (not holds) at 14:15. Keep every bar in df so an open
# trade can be managed to the close, and so labels can see the held path.
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

# Only need target/stop where we could actually enter -> compute on those rows.
df[['Target_Up', 'Target_Down', 'Stop_Up', 'Stop_Down']] = np.nan
elig_mask = df['Entry_Eligible'].values
if elig_mask.any():
    mat = df.loc[elig_mask].apply(calculate_dynamic_risk_matrix, axis=1)
    df.loc[elig_mask, ['Target_Up', 'Target_Down', 'Stop_Up', 'Stop_Down']] = mat.values

# ---- Precompute SAME-DAY positional boundaries  [FIX 1] -------------------
date_arr = df['date'].values
n = len(df)
day_end_pos = np.empty(n, dtype=np.int64)   # positional index of the last bar that day
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

# [FIX 1 + FIX 4] Walk-forward path, clipped to the SAME DAY, scored intrabar
# with high/low. If a bar spans both target and stop, the STOP is assumed
# first (pessimistic) so winners are never over-counted.
for i in range(n):
    if not elig_arr[i]:
        continue

    end = min(i + 1 + HOLD_BARS, day_end_pos[i] + 1)   # never crosses into next day
    if end <= i + 1:
        continue
    hi = es_high_arr[i + 1:end]
    lo = es_low_arr[i + 1:end]

    # bullish path
    t_up, s_up = target_up_arr[i], stop_up_arr[i]
    if not (np.isnan(t_up) or np.isnan(s_up)):
        for b in range(len(hi)):
            if lo[b] <= s_up:        # stop first
                break
            if hi[b] >= t_up:
                bull_targets[i] = 1
                break

    # bearish path
    t_dn, s_dn = target_down_arr[i], stop_down_arr[i]
    if not (np.isnan(t_dn) or np.isnan(s_dn)):
        for b in range(len(hi)):
            if hi[b] >= s_dn:        # stop first
                break
            if lo[b] <= t_dn:
                bear_targets[i] = 1
                break

df['bull_breakout'] = bull_targets
df['bear_breakout'] = bear_targets

# Target = the break succeeded in the direction the active delta is pushing.
df['TARGET_BREAKOUT'] = np.where(
    ((df['Active_Vol_Delta'] > 0) & (df['bull_breakout'] == 1)) |
    ((df['Active_Vol_Delta'] < 0) & (df['bear_breakout'] == 1)),
    1, 0
)

# df_breaks = the rows the model is actually allowed to act on (eligible entries)
df_breaks = df[df['Entry_Eligible']].copy()

# =======================================================================
# 6. XGBOOST TRAINING  (train -> validate -> blind test, all time-separated)
# =======================================================================
ml_features = [
    'At_Level', 'At_Wall', 'At_King', 'At_POC',
    'Active_Vol_Delta', 'Active_Trade_Delta', 'Active_Mid_Vol',
    'Cumulative_3Min_Delta', 'Delta_Momentum', 'Price_Velocity_3Min',
    'net_delta', 'net_gamma', 'net_vanna', 'net_charm', 'net_vomma'
] + features_to_shift

TRAIN_END = '2026-04-15'
TEST_START, TEST_END = '2026-04-16', '2026-04-30'

# [FIX 3] Hold out the last 15 TRAIN days as a validation slice for threshold
# tuning, so the threshold is never set on rows the model was fit on.
train_pool_dates = sorted(df_breaks[df_breaks['date'] <= TRAIN_END]['date'].unique())
val_dates = set(train_pool_dates[-15:]) if len(train_pool_dates) > 20 else set()

df_train = df_breaks[(df_breaks['date'] <= TRAIN_END) & (~df_breaks['date'].isin(val_dates))]
df_val = df_breaks[df_breaks['date'].isin(val_dates)]
df_test = df_breaks[(df_breaks['date'] >= TEST_START) & (df_breaks['date'] <= TEST_END)]

if df_train.empty or df_train['TARGET_BREAKOUT'].nunique() < 2:
    print("FATAL: training set is empty or single-class. Check dates / data.")
    raise SystemExit(1)

X_train, y_train = df_train[ml_features], df_train['TARGET_BREAKOUT']
pos_n = int((y_train == 1).sum())
neg_n = int((y_train == 0).sum())
pos_weight = neg_n / pos_n if pos_n > 0 else 1.0

print(f"   train rows={len(df_train)}  (pos={pos_n}, neg={neg_n}, base rate={pos_n/len(df_train)*100:.1f}%)")
print(f"   val rows={len(df_val)}   test rows={len(df_test)}")
print("   TRAINING V41 (direct breakout engine)...")

model = xgb.XGBClassifier(
    objective='binary:logistic', n_estimators=550, learning_rate=0.02,
    max_depth=5, subsample=0.8, colsample_bytree=0.8, scale_pos_weight=pos_weight,
    random_state=42, eval_metric='auc', verbosity=0
)
model.fit(X_train, y_train)

# [FIX 2 + FIX 3] Tune threshold on the VALIDATION slice, with correct
# precision_recall_curve alignment (thresholds is one shorter than precisions).
if not df_val.empty and df_val['TARGET_BREAKOUT'].nunique() == 2:
    thr_X, thr_y = df_val[ml_features], df_val['TARGET_BREAKOUT']
    thr_src = "validation"
else:
    thr_X, thr_y = X_train, y_train
    thr_src = "train (no usable validation slice)"

calib_probs = model.predict_proba(thr_X)[:, 1]
precisions, recalls, thresholds = precision_recall_curve(thr_y, calib_probs)
p, r = precisions[:-1], recalls[:-1]                       # align to thresholds
f1_scores = 2 * (p * r) / (p + r + 1e-9)
opt_thresh = float(thresholds[int(np.argmax(f1_scores))])
opt_thresh = min(max(opt_thresh, 0.40), 0.60)

print(f"   breakout threshold = {opt_thresh*100:.2f}%  (tuned on {thr_src})\n")

# =======================================================================
# 7. BLIND TEST  -  next-bar fill, intrabar exits, one trade at a time
# =======================================================================
print("=====================================================================")
print(" BLIND TEST (Apr 16-30): next-bar fill, intrabar stops, EOD-flat ")
print("=====================================================================")
print(f"{'DATE':<12} | {'CAUGHT':<8} | {'STOPPED':<8} | {'TIMED OUT':<10} | {'WIN RATE':<10}")
print("-" * 65)

# [FIX 5] Precompute probabilities once for every eligible structure bar in the
# test window (vectorized), then look them up - and it removes the per-row
# predict_proba call inside the loop.
test_dates = sorted(df_test['date'].unique())
elig_test = df[(df['date'].isin(test_dates)) & (df['Entry_Eligible'])]
prob_map = {}
if not elig_test.empty:
    elig_probs = model.predict_proba(elig_test[ml_features])[:, 1]
    prob_map = dict(zip(elig_test.index, elig_probs))

total_caught, total_stopped, total_timeouts = 0, 0, 0
trade_log = []

for d in test_dates:
    daily_df = df[df['date'] == d]
    last_idx = daily_df.index[-1]

    in_trade = False
    pending = 0                 # 0 = none, 1 = long signal armed, -1 = short signal armed
    pend_t = pend_s = 0.0
    direction = 0
    target = stop = 0.0
    bars_in_trade = 0
    caught = stopped = timeouts = 0

    for idx, row in daily_df.iterrows():
        # 1) Manage an open trade (intrabar high/low; stop checked first).
        if in_trade:
            bars_in_trade += 1
            hi, lo = row['es_high'], row['es_low']
            done, tag = False, ''
            if direction == 1:
                if lo <= stop:
                    stopped += 1; done, tag = True, 'STOPPED (LOSS)'
                elif hi >= target:
                    caught += 1; done, tag = True, 'TARGET HIT (WIN)'
            else:
                if hi >= stop:
                    stopped += 1; done, tag = True, 'STOPPED (LOSS)'
                elif lo <= target:
                    caught += 1; done, tag = True, 'TARGET HIT (WIN)'
            if not done and bars_in_trade >= HOLD_BARS:
                timeouts += 1; done, tag = True, 'TIME EXIT (CHOP)'
            if not done and idx == last_idx:
                timeouts += 1; done, tag = True, 'EOD FLAT'
            if done:
                in_trade = False
                trade_log.append({'date': d, 'time': row['time_ct'], 'type': tag, 'price': row['es_close']})
            continue

        # 2) [FIX 5] Fill a signal armed on the PREVIOUS bar (next-bar entry).
        if pending != 0:
            in_trade = True
            direction = pending
            target, stop = pend_t, pend_s
            bars_in_trade = 0
            pending = 0
            etype = 'ENTRY UP' if direction == 1 else 'ENTRY DOWN'
            trade_log.append({'date': d, 'time': row['time_ct'], 'type': etype, 'price': row['es_close']})
            continue

        # 3) Look for a fresh breakout signal at an eligible structure bar.
        prob = prob_map.get(idx)
        if prob is not None and prob >= opt_thresh:
            dlt = row['Active_Vol_Delta']
            vel = row['Price_Velocity_3Min']
            if dlt > 0 and vel >= -0.5:
                pending = 1; pend_t, pend_s = row['Target_Up'], row['Stop_Up']
            elif dlt < 0 and vel <= 0.5:
                pending = -1; pend_t, pend_s = row['Target_Down'], row['Stop_Down']

    decided = caught + stopped + timeouts
    win_rate = (caught / decided * 100) if decided > 0 else 0.0
    print(f"{d:<12} | {caught:<8} | {stopped:<8} | {timeouts:<10} | {win_rate:>6.2f}%")
    total_caught += caught; total_stopped += stopped; total_timeouts += timeouts

print("-" * 65)
decided = total_caught + total_stopped + total_timeouts
agg_win_rate = (total_caught / decided * 100) if decided > 0 else 0.0
print(f"{'AGGREGATE':<12} | {total_caught:<8} | {total_stopped:<8} | {total_timeouts:<10} | {agg_win_rate:>6.2f}%")
print("=====================================================================")

print("\nFEATURE IMPORTANCE (top 15)")
importance = model.feature_importances_
feature_rank = pd.DataFrame({'Feature': ml_features, 'Importance': importance})
feature_rank = feature_rank.sort_values(by='Importance', ascending=False).head(15)
feature_rank['Importance'] = (feature_rank['Importance'] * 100).map('{:.2f}%'.format)
print(feature_rank.to_string(index=False))

# =======================================================================
# 8. AUDIT TOOL (first test day trade-by-trade)
# =======================================================================
print("\n=====================================================================")
print(f" TRADE AUDIT: {TEST_START} ")
print("=====================================================================")
day1_trades = [t for t in trade_log if t['date'] == TEST_START]
if day1_trades:
    for t in day1_trades:
        print(f"{t['time']} | {t['type']:<20} | PRICE: {t['price']:.2f}")
else:
    print(f"No trades triggered on {TEST_START}.")
print("=====================================================================\n")

model.save_model("Apex_V41_Audited.json")
print("saved model -> Apex_V41_Audited.json")
