# ============================================================================
#  ES STRUCTURE SWEEP  -  every rule parameter of the breakout engine, swept,
#  scored on net P&L after costs, judged OUT-OF-SAMPLE and against a placebo.
# ============================================================================
#  Same idea as the Saty sweep: stop asking "does the default work?" and ask
#  "is there ANY setting with an edge that survives honest testing?"
#
#  This does NOT use the XGBoost model. It tests the raw rule layer the model
#  sits on (levels + order flow). If no rule setting has an edge, a model on
#  top of it is fitting noise. It does not touch apex_v41_audited.py.
#
#  WHAT IS SWEPT
#    zone           2, 4, 6 pts             how close to a level counts as "at" it (V41: 4)
#    level set      orb / value / walls / all, walls raw or shifted by the
#                   live ES-SPX basis       which levels fire (V41: all, raw)
#    movement       flow    trade WITH order-flow delta at the level (V41)
#                   fade    trade AGAINST order-flow delta at the level
#                   break   close crosses THROUGH the level -> go with it
#                   reject  bar pokes through and closes back -> fade it
#    delta filter   none / strong |delta| / delta with / against the trade
#    gamma regime   any / Net_GEX > 0 / Net_GEX < 0
#    entry window   09:15-10:30, 10:30-12:00, 12:00-14:15, 09:15-14:15 CT
#    exit           levels (V41 next-level target/stop) or fixed stop 3/6 pts
#                   with target 1x or 2x the stop
#    hold           15, 45, 90 bars
#
#  HOW EACH SETTING IS SCORED (no leaks)
#    - signal on bar close, fill at the NEXT bar's OPEN
#    - exits from the fill bar on, intrabar high/low, STOP first if a bar
#      spans both, gaps through a stop exit at the open
#    - one trade at a time, flat by the last bar of the day, never overnight
#    - COST_PTS charged per round trip
#    - first IS_FRAC of days = in-sample (pick), the rest = out-of-sample (judge)
#    - PLACEBO: the same entries with a RANDOM direction. If the real best
#      setting is no better than the placebo's best, the "edge" is chance.
#
#  Run:   python es_sweep_all.py [path\to\APEX_FINAL_SQUEEZE_BRAIN.csv]
#         python es_sweep_all.py --synthetic     (random-walk data, sanity check)
# ============================================================================

import sys
import itertools
import time
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

FILE = "APEX_FINAL_SQUEEZE_BRAIN.csv"
COST_PTS = 0.50             # round-trip cost per ES trade, points
POINT_VALUE = 50.0          # ES $ per point (MES = 5.0)
RTH_END = "15:00:00"        # flat by the last bar at or before this time
IS_FRAC = 2 / 3             # first 2/3 of days in-sample, last 1/3 out-of-sample
MIN_TRADES = 30             # in-sample trades needed for a setting to be ranked
SEED = 7

ZONES = [2.0, 4.0, 6.0]
LEVEL_SETS = ['orb', 'value', 'walls_raw', 'walls_es', 'all_raw', 'all_es']
MOVES = {'flow': ['none', 'strong'], 'fade': ['none', 'strong'],
         'break': ['none', 'strong', 'with', 'against'],
         'reject': ['none', 'strong', 'with', 'against']}
GEX = ['any', 'pos', 'neg']
WINDOWS = {'0915-1030': ('09:15:00', '10:30:00'), '1030-1200': ('10:30:00', '12:00:00'),
           '1200-1415': ('12:00:00', '14:15:00'), '0915-1415': ('09:15:00', '14:15:00')}
EXITS = ['levels', 'S3R1', 'S3R2', 'S6R1', 'S6R2']
HOLDS = [15, 45, 90]
MAX_HOLD = max(HOLDS)

args = sys.argv[1:]
SYNTH = '--synthetic' in args
paths = [a for a in args if not a.startswith('--')]

print("\n=====================================================================")
print(" [APEX] ES STRUCTURE SWEEP  (net P&L, out-of-sample, vs placebo)")
print("=====================================================================")


# =======================================================================
# 1. DATA
# =======================================================================
def synthetic_brain():
    """Pure random walk with the brain schema. Any 'edge' found here is noise."""
    rng = np.random.default_rng(0)
    rows, px = [], 6000.0
    for d in pd.bdate_range('2025-11-19', '2026-05-18'):
        ds = d.strftime('%Y-%m-%d')
        kn = round((px - 30) / 25) * 25
        gex = rng.choice([-1e9, 1e9])
        for m in range(391):
            t = (pd.Timestamp(ds) + pd.Timedelta(hours=8, minutes=30 + m)).strftime('%H:%M:%S')
            o = px; px += rng.normal(0, 1.2); c = px
            h, l = max(o, c) + abs(rng.normal(0, .5)), min(o, c) - abs(rng.normal(0, .5))
            f = rng.poisson(50, 6)
            rows.append([ds, t, c - 30, f[0], f[1], f[2], f[3], o, h, l, c,
                         rng.poisson(1000), kn + 50, kn - 50, kn, gex])
    return pd.DataFrame(rows, columns=['date', 'time_ct', 'spx_close', 'call_vol_ask', 'call_vol_bid',
                                       'put_vol_ask', 'put_vol_bid', 'es_open', 'es_high', 'es_low',
                                       'es_close', 'es_vol', 'Call_Wall', 'Put_Wall', 'King_Node',
                                       'Net_GEX'])


if SYNTH:
    print("   DATA: SYNTHETIC random walk (no edge exists - this checks the test itself)")
    df = synthetic_brain()
else:
    path = paths[0] if paths else FILE
    print(f"   DATA: {path}")
    df = pd.read_csv(path)
df.dropna(subset=['spx_close', 'es_open', 'es_high', 'es_low', 'es_close'], inplace=True)
df['date'] = df['date'].astype(str)
df = df[(df['time_ct'] >= '08:30:00') & (df['time_ct'] <= RTH_END)]
df = df.sort_values(['date', 'time_ct']).reset_index(drop=True)

# =======================================================================
# 2. LEVELS  (ORB + 70% value area from 08:30-09:00 only, same as V41)
# =======================================================================
records = []
for d, day in df[df['time_ct'] < '09:00:00'].groupby('date'):
    vp = (day.assign(tick=np.round(day['es_close'] * 4) / 4)
             .groupby('tick')['es_vol'].sum().sort_index().reset_index())
    if vp.empty:
        continue
    poc_i = int(vp['es_vol'].values.argmax())
    vols, target = vp['es_vol'].values, vp['es_vol'].sum() * 0.70
    lo = hi = poc_i
    cur = vols[poc_i]
    while cur < target and (hi < len(vp) - 1 or lo > 0):
        up = vols[hi + 1] if hi < len(vp) - 1 else 0
        dn = vols[lo - 1] if lo > 0 else 0
        if up >= dn and hi < len(vp) - 1:
            hi += 1; cur += up
        elif lo > 0:
            lo -= 1; cur += dn
    records.append({'date': d, 'ORB_High': day['es_high'].max(), 'ORB_Low': day['es_low'].min(),
                    'Anchored_POC': vp['tick'][poc_i], 'Anchored_VAH': vp['tick'][hi],
                    'Anchored_VAL': vp['tick'][lo]})
df = df.merge(pd.DataFrame(records), on='date', how='inner')

# Option-strike levels in ES terms: trailing same-day median of es - spx.
basis = (df['es_close'] - df['spx_close']).groupby(df['date']).transform(
    lambda x: x.rolling(15, min_periods=1).median())
if abs(basis.median()) > 200:
    print(f"   WARNING: median ES-SPX basis {basis.median():+.1f} is not plausible; "
          "the *_es level sets equal the raw ones.")
    basis = basis * 0
print(f"   ES-SPX basis: median {basis.median():+.2f} pts")
for c in ['Call_Wall', 'Put_Wall', 'King_Node']:
    df[c + '_es'] = df[c] + basis

df['delta'] = (df['call_vol_ask'] - df['call_vol_bid'] + df['put_vol_bid'] - df['put_vol_ask'])
df['abs_med'] = df['delta'].abs().groupby(df['date']).transform(
    lambda x: x.rolling(60, min_periods=10).median())
df['prev_close'] = df.groupby('date')['es_close'].shift(1)
df = df[df['time_ct'] >= '09:00:00'].reset_index(drop=True)

O, H, L, C = (df[k].values for k in ['es_open', 'es_high', 'es_low', 'es_close'])
n = len(df)
dates = df['date'].values
day_end = np.empty(n, dtype=np.int64)
start = 0
for k in range(1, n + 1):
    if k == n or dates[k] != dates[start]:
        day_end[start:k] = k - 1
        start = k

all_days = sorted(df['date'].unique())
split_day = all_days[int(len(all_days) * IS_FRAC)]
split_pos = int(np.searchsorted(dates, split_day))
print(f"   {len(all_days)} days {all_days[0]} -> {all_days[-1]}   "
      f"in-sample < {split_day} <= out-of-sample")
if 'Net_GEX' not in df.columns:
    GEX = ['any']
    print("   Net_GEX missing -> gamma regime not swept")

# Candidate signal bars: inside the widest entry window, with a next bar to fill on.
times = df['time_ct'].values
cand = np.nonzero((times >= '09:15:00') & (times < '14:15:00') & (np.arange(n) < day_end))[0]
m = len(cand)
cC, cH, cL = C[cand], H[cand], L[cand]
cprev = df['prev_close'].values[cand]
cdelta = df['delta'].values[cand]
sgn = np.sign(cdelta)
strong = np.abs(cdelta) > 2 * np.nan_to_num(df['abs_med'].values[cand], nan=np.inf)
ctime = times[cand]
cgex = df['Net_GEX'].values[cand] if 'Net_GEX' in df.columns else np.zeros(m)

# =======================================================================
# 3. EVERY EXIT OUTCOME, PRECOMPUTED ONCE (vectorized forward paths)
# =======================================================================
t0 = time.time()
k_ar = np.arange(MAX_HOLD)
fidx = cand[:, None] + 1 + k_ar
fvalid = fidx <= day_end[cand][:, None]
fidx = np.minimum(fidx, n - 1)
HF, LF = np.where(fvalid, H[fidx], np.nan), np.where(fvalid, L[fidx], np.nan)
OF, CF = O[fidx], C[fidx]
lastk = day_end[cand] - cand - 1           # last forward step still in the day
entry = O[cand + 1]
rows_m = np.arange(m)


def simulate(stop, tgt, d):
    """{hold: (gross pts, exit position)} for a trade in direction d from every candidate."""
    if d == 1:
        sh, th = LF <= stop[:, None], HF >= tgt[:, None]
    else:
        sh, th = HF >= stop[:, None], LF <= tgt[:, None]
    big = MAX_HOLD + 1
    fs = np.where(sh.any(1), sh.argmax(1), big)
    ft = np.where(th.any(1), th.argmax(1), big)
    out = {}
    for hold in HOLDS:
        lim = np.minimum(hold - 1, lastk)
        stop_first = (fs <= lim) & (fs <= ft)
        tgt_first = (ft <= lim) & ~stop_first
        k = np.where(stop_first, fs, np.where(tgt_first, ft, lim))
        ok, ck = OF[rows_m, k], CF[rows_m, k]
        if d == 1:
            px = np.where(stop_first, np.minimum(ok, stop), np.where(tgt_first, np.maximum(ok, tgt), ck))
        else:
            px = np.where(stop_first, np.maximum(ok, stop), np.where(tgt_first, np.minimum(ok, tgt), ck))
        out[hold] = ((px - entry) * d, cand + 1 + k)
    return out


def level_exits(cols):
    """V41's dynamic matrix: nearest level >=3 pts away = target, >=2 pts behind = stop."""
    M, c = df[cols].values[cand].astype(float), cC[:, None]
    def pick(mask, fn, fill, fallback):
        v = fn(np.where(mask, M, fill), axis=1)
        return np.where(np.isfinite(v), v, fallback)
    t_up = pick(M >= c + 3, np.min, np.inf, cC + 10)
    t_dn = pick(M <= c - 3, np.max, -np.inf, cC - 10)
    s_up = pick(M <= c - 2, np.max, -np.inf, cC - 6)
    s_dn = pick(M >= c + 2, np.min, np.inf, cC + 6)
    return (s_up, t_up), (s_dn, t_dn)


V41_LEVELS = ['Anchored_POC', 'Anchored_VAH', 'Anchored_VAL', 'ORB_High', 'ORB_Low']
outcomes = {}
for name, walls in [('lvl_raw', ['Call_Wall', 'Put_Wall']), ('lvl_es', ['Call_Wall_es', 'Put_Wall_es'])]:
    (su, tu), (sd, td) = level_exits(V41_LEVELS + walls)
    outcomes[(name, 1)], outcomes[(name, -1)] = simulate(su, tu, 1), simulate(sd, td, -1)
for s, r in [(3, 1), (3, 2), (6, 1), (6, 2)]:
    outcomes[(f'S{s}R{r}', 1)] = simulate(entry - s, entry + r * s, 1)
    outcomes[(f'S{s}R{r}', -1)] = simulate(entry + s, entry - r * s, -1)
print(f"   precomputed exits for {m:,} candidate bars in {time.time() - t0:.1f}s")

# =======================================================================
# 4. THE SWEEP
# =======================================================================
SET_COLS = {
    'orb': ['ORB_High', 'ORB_Low'],
    'value': ['Anchored_VAH', 'Anchored_VAL', 'Anchored_POC'],
    'walls_raw': ['Call_Wall', 'Put_Wall', 'King_Node'],
    'walls_es': ['Call_Wall_es', 'Put_Wall_es', 'King_Node_es'],
}
SET_COLS['all_raw'] = SET_COLS['orb'] + SET_COLS['value'] + SET_COLS['walls_raw']
SET_COLS['all_es'] = SET_COLS['orb'] + SET_COLS['value'] + SET_COLS['walls_es']

rand_dir = np.random.default_rng(SEED).choice([-1, 1], size=m)
win_mask = {w: (ctime >= a) & (ctime < b) for w, (a, b) in WINDOWS.items()}
gex_mask = {'any': np.ones(m, bool), 'pos': cgex > 0, 'neg': cgex < 0}


def stats(net, pos):
    out = {}
    for tag, sel in [('is', pos < split_pos), ('oos', pos >= split_pos), ('all', np.ones(len(pos), bool))]:
        x = net[sel]
        k = len(x)
        mu = x.mean() if k else np.nan
        sd = x.std(ddof=1) if k > 1 else np.nan
        out[f'n_{tag}'] = k
        out[f'mean_{tag}'] = mu
        out[f't_{tag}'] = mu / (sd / np.sqrt(k)) if k > 1 and sd > 0 else np.nan
        out[f'tot_{tag}'] = x.sum()
    return out


def take_trades(pos, ex):
    """One trade at a time: after a trade exits, the next signal must be on a later bar."""
    nxt = np.searchsorted(pos, ex, side='right')
    taken, j, k = [], 0, len(pos)
    while j < k:
        taken.append(j)
        j = nxt[j]
    return np.array(taken, dtype=np.int64)


results = []
t0 = time.time()
for zone, lset in itertools.product(ZONES, LEVEL_SETS):
    M = df[SET_COLS[lset]].values[cand].astype(float)
    dist = np.abs(M - cC[:, None])
    dist = np.where(np.isnan(dist), np.inf, dist)
    j = dist.argmin(1)
    lvl = M[rows_m, j]
    near = dist[rows_m, j] <= zone
    up_x = (cprev < lvl) & (cC >= lvl)
    dn_x = (cprev > lvl) & (cC <= lvl)
    dirs = {
        'flow': sgn,
        'fade': -sgn,
        'break': np.where(up_x, 1, np.where(dn_x, -1, 0)),
        'reject': np.where((cH >= lvl) & (cC < lvl) & (cprev < lvl), -1,
                           np.where((cL <= lvl) & (cC > lvl) & (cprev > lvl), 1, 0)),
    }
    lvl_exit = 'lvl_es' if lset.endswith('_es') else 'lvl_raw'
    for move, filts in MOVES.items():
        dvec = np.where(near, dirs[move], 0).astype(int)
        fmask = {'none': np.ones(m, bool), 'strong': strong,
                 'with': sgn == dvec, 'against': sgn == -dvec}
        for filt, gx, win in itertools.product(filts, GEX, WINDOWS):
            rows = np.nonzero((dvec != 0) & fmask[filt] & gex_mask[gx] & win_mask[win])[0]
            if len(rows) < MIN_TRADES:
                continue
            pos, dsel, psel = cand[rows], dvec[rows], rand_dir[rows]
            for ex, hold in itertools.product(EXITS, HOLDS):
                key = lvl_exit if ex == 'levels' else ex
                pl, el = outcomes[(key, 1)][hold]
                ps, es = outcomes[(key, -1)][hold]
                rec = {'zone': zone, 'levels': lset, 'move': move, 'filter': filt, 'gex': gx,
                       'window': win, 'exit': ex, 'hold': hold}
                for tag, dd in [('', dsel), ('pl_', psel)]:      # real, then placebo direction
                    pnl = np.where(dd > 0, pl[rows], ps[rows])
                    exitp = np.where(dd > 0, el[rows], es[rows])
                    tk = take_trades(pos, exitp)
                    st = stats(pnl[tk] - COST_PTS, pos[tk])
                    rec.update({tag + k: v for k, v in st.items()})
                results.append(rec)
R = pd.DataFrame(results)
print(f"   swept {len(R):,} settings (+ the same number of placebo runs) in {time.time() - t0:.0f}s")
R.to_csv('es_sweep_results.csv', index=False)
print("   full table -> es_sweep_results.csv")

# =======================================================================
# 5. REPORT
# =======================================================================
ranked = R[R['n_is'] >= MIN_TRADES]
cols = ['zone', 'levels', 'move', 'filter', 'gex', 'window', 'exit', 'hold']


def show(df_, title, k=12):
    print(f"\n   {title}")
    print(f"   {'zone':>4} {'levels':<9} {'move':<6} {'filter':<7} {'gex':<3} {'window':<9} "
          f"{'exit':<6} {'hold':>4} | {'n IS':>5} {'IS pts':>7} {'t IS':>5} | "
          f"{'n OOS':>5} {'OOS pts':>7} {'t OOS':>6} {'OOS $':>9}")
    for _, q in df_.head(k).iterrows():
        print(f"   {q.zone:>4.0f} {q.levels:<9} {q.move:<6} {q['filter']:<7} {q.gex:<3} {q.window:<9} "
              f"{q.exit:<6} {int(q.hold):>4} | {int(q.n_is):>5} {q.mean_is:>+7.2f} {q.t_is:>+5.1f} | "
              f"{int(q.n_oos):>5} {q.mean_oos:>+7.2f} {q.t_oos:>+6.1f} {q.tot_oos * POINT_VALUE:>+9,.0f}")


print("\n=====================================================================")
print(" A. THE V41 RULE ITSELF (zone 4, all levels raw, flow, levels exit, hold 45)")
print("=====================================================================")
v41 = R[(R.zone == 4) & (R.levels == 'all_raw') & (R.move == 'flow') & (R['filter'] == 'none')
        & (R.gex == 'any') & (R.window == '0915-1415') & (R.exit == 'levels') & (R.hold == 45)]
show(v41, "every signal, no model (no velocity filter):", 1)

print("\n=====================================================================")
print(" B. PICK ON IN-SAMPLE, JUDGE ON OUT-OF-SAMPLE  (the honest number)")
print("=====================================================================")
top_real = ranked.sort_values('t_is', ascending=False)
show(top_real, "top settings by in-sample t-stat, and what they did afterwards:")
top_pl = ranked.sort_values('pl_t_is', ascending=False)
k = min(20, len(top_real))
print(f"\n   top-{k} by IS:   real  avg OOS {top_real.head(k)['mean_oos'].mean():+.3f} pts/trade"
      f"   |   placebo (random direction) avg OOS {top_pl.head(k)['pl_mean_oos'].mean():+.3f}")
print(f"   best IS t-stat:  real {top_real['t_is'].max():+.2f}   placebo {top_pl['pl_t_is'].max():+.2f}"
      "   (real must clearly beat placebo, or the best setting is chance)")
both = ranked.dropna(subset=['mean_is', 'mean_oos'])
rho = both['mean_is'].rank().corr(both['mean_oos'].rank())
print(f"   rank correlation IS vs OOS across all settings: {rho:+.2f}   "
      "(near 0 = in-sample ranking predicts nothing)")

print("\n=====================================================================")
print(" C. HOW MANY SETTINGS LOOK 'SIGNIFICANT'  (full sample, t > 2)")
print("=====================================================================")
real_sig = int((ranked['t_all'] > 2).sum())
pl_sig = int((ranked['pl_t_all'] > 2).sum())
print(f"   real: {real_sig:,} of {len(ranked):,}   placebo (random direction): {pl_sig:,} of {len(ranked):,}"
      "   (real should be far above placebo)")
both_pos = ranked[(ranked['t_is'] > 2) & (ranked['t_oos'] > 2)]
print(f"   significant in BOTH halves (t>2 IS and t>2 OOS): {len(both_pos):,}")
if len(both_pos):
    show(both_pos.sort_values('t_oos', ascending=False), "those settings:")

print("\n=====================================================================")
print(" D. WHAT HELPS / WHAT HURTS  (median OOS pts/trade per parameter value)")
print("=====================================================================")
judged = ranked[ranked['n_oos'] >= MIN_TRADES // 2]
for c in cols:
    g = judged.groupby(c).agg(settings=('mean_oos', 'size'), median_oos=('mean_oos', 'median'),
                              share_pos=('mean_oos', lambda x: (x > 0).mean()),
                              placebo=('pl_mean_oos', 'median'))
    print(f"\n   {c}:")
    for v, q in g.sort_values('median_oos', ascending=False).iterrows():
        print(f"      {str(v):<10} settings={int(q.settings):>5}  median OOS {q.median_oos:+.3f}  "
              f"positive {q.share_pos:>4.0%}   placebo {q.placebo:+.3f}")
print(f"\n   (costs are {COST_PTS} pts per trade, so a no-edge setting sits near -{COST_PTS})")
print("=====================================================================\n")
