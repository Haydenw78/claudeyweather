"""Is the fit stable, or did the search just find noise?"""
import json, statistics, random
from engine import DEFAULTS, ceiling
from stats import spearman, deseason
from fit_util import load, score, kfold

base = dict(DEFAULTS)
fitted = dict(DEFAULTS); fitted.update(wEkman=18, windFull=22, windExp=0.7)
nsi = load('North Stradbroke Island')

print("Stability across resampling seeds (5-fold, de-seasonalised Spearman)\n")
print("  seed   current   fitted")
cur, fit = [], []
for seed in range(1, 11):
    a = kfold(nsi, base, seed=seed); b = kfold(nsi, fitted, seed=seed)
    cur.append(a); fit.append(b)
    print(f"   {seed:2d}    {a:+.3f}    {b:+.3f}")
print(f"\n  mean   {statistics.mean(cur):+.3f}    {statistics.mean(fit):+.3f}")
print(f"  sd     {statistics.stdev(cur):.3f}     {statistics.stdev(fit):.3f}")

# hold out whole years: the hardest test, since it removes any within-year leakage
print("\nLeave-one-year-out")
years = sorted({r['year'] for r in nsi})
ca, fa = [], []
for y in years:
    test = [r for r in nsi if r['year'] == y]
    if len(test) < 8: continue
    ca.append(score(test, base)); fa.append(score(test, fitted))
print(f"  current {statistics.mean(ca):+.3f}   fitted {statistics.mean(fa):+.3f}   over {len(ca)} years")

# independent station
yon = load('Yongala')
if len(yon) >= 12:
    print(f"\nHeld-out station, Yongala n={len(yon)}")
    print(f"  current {score(yon, base):+.3f}   fitted {score(yon, fitted):+.3f}")
