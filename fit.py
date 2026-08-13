"""
Fit the ceiling weights against observed clarity, with k-fold cross-validation
so that improvements have to generalise rather than just memorise.
"""
import json, math, random, statistics, collections, itertools
from engine import DEFAULTS, ceiling, wind_mix, to_metres
from stats import spearman, deseason, crit

d = json.load(open('data.json'))
ref = d['reference']

def load(station):
    rs = [r for r in ref if r['station'] == station]
    for r in rs:
        r['month'] = int(r['ts'][5:7])
        r['year']  = int(r['ts'][:4])
    return rs

def score(recs, p, season=None):
    """Spearman of predicted index against observed clarity, de-seasonalised."""
    months = [r['month'] for r in recs]
    pred = [ceiling(r, p, (season or {}).get(r['month'], 0.0)) for r in recs]
    obs  = [r['visObservedM'] for r in recs]
    return spearman(deseason(pred, months), deseason(obs, months))

def kfold(recs, p, k=5, seed=1, season=None):
    rs = recs[:]; random.Random(seed).shuffle(rs)
    folds = [rs[i::k] for i in range(k)]
    out = []
    for i in range(k):
        test = folds[i]
        if len(test) < 12: continue
        out.append(score(test, p, season))
    return statistics.mean(out) if out else 0.0

nsi = load('North Stradbroke Island')
print(f"North Stradbroke, n={len(nsi)}\n")

base = dict(DEFAULTS)
print(f"  current weights   in-sample {score(nsi, base):+.3f}   cross-validated {kfold(nsi, base):+.3f}")

# coordinate search over the terms an export can actually inform
grid = {
    'wWind':    [0, 8, 15, 22, 30, 40, 55],
    'wEkman':   [0, 5, 10, 18, 28],
    'wRain':    [0, 0.4, 0.9, 1.6, 2.5],
    'windOn':   [0, 4, 8, 12],
    'windFull': [22, 28, 34, 45],
    'windExp':  [0.7, 1.0, 1.15, 1.5],
}
p = dict(base)
for _ in range(4):
    for k, opts in grid.items():
        best, bv = p[k], kfold(nsi, p)
        for v in opts:
            q = dict(p); q[k] = v
            sc = kfold(nsi, q)
            if sc > bv: best, bv = v, sc
        p[k] = best
print(f"  fitted weights    in-sample {score(nsi, p):+.3f}   cross-validated {kfold(nsi, p):+.3f}")
print("\n  fitted values:")
for k in grid:
    if p[k] != base[k]: print(f"    {k:9s} {base[k]:>6} -> {p[k]:>6}")
    else:               print(f"    {k:9s} {base[k]:>6}    (unchanged)")

# what does a wind-only model achieve? if it matches, the rest is decoration
wind_only = dict(base); wind_only.update(wStir=0, wEkman=0, wSst=0, wRain=0)
print(f"\n  wind term alone   cross-validated {kfold(nsi, wind_only):+.3f}")
null = dict(base); null.update(wStir=0, wWind=0, wEkman=0, wSst=0, wRain=0)
print(f"  constant model    cross-validated {kfold(nsi, null):+.3f}")
