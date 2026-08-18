"""Is the fit stable, or did the search just find noise?"""
import json, os, statistics, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # forecast_client.py lives at repo root
from forecast_client import ForecastEngine, STIRLAG_MISSING_NOTE
from stats import spearman, deseason
from fit_util import load, score, kfold, report_refusal

with ForecastEngine() as fc:
    base = dict(fc.constants()['WEIGHTS'])
    fitted = dict(base); fitted.update(wEkman=18, windDen=22, windExp=0.7)
    nsi = load('North Stradbroke Island')

    print("Stability across resampling seeds (5-fold, de-seasonalised Spearman)\n")
    print("  seed   current   fitted")
    cur, fit = [], []
    seed_refused = 0
    for seed in range(1, 11):
        a, ra = kfold(nsi, base, seed=seed, fc=fc)
        b, rb = kfold(nsi, fitted, seed=seed, fc=fc)
        seed_refused += ra + rb
        cur.append(a); fit.append(b)
        print(f"   {seed:2d}    {a:+.3f}    {b:+.3f}")
    print(f"\n  mean   {statistics.mean(cur):+.3f}    {statistics.mean(fit):+.3f}")
    print(f"  sd     {statistics.stdev(cur):.3f}     {statistics.stdev(fit):.3f}")
    report_refusal('seed sweep', seed_refused, len(nsi) * 20)

    # hold out whole years: the hardest test, since it removes any within-year leakage
    print("\nLeave-one-year-out")
    years = sorted({r['year'] for r in nsi})
    ca, fa = [], []
    loyo_refused = loyo_attempted = 0
    for y in years:
        test = [r for r in nsi if r['year'] == y]
        if len(test) < 8: continue
        sa, ra = score(test, base, fc=fc)
        sb, rb = score(test, fitted, fc=fc)
        loyo_refused += ra + rb
        loyo_attempted += len(test) * 2
        ca.append(sa); fa.append(sb)
    if ca:
        print(f"  current {statistics.mean(ca):+.3f}   fitted {statistics.mean(fa):+.3f}   over {len(ca)} years")
    else:
        print("  no years scored")
    report_refusal('leave-one-year-out', loyo_refused, loyo_attempted)

    # independent station
    yon = load('Yongala')
    if len(yon) >= 12:
        print(f"\nHeld-out station, Yongala n={len(yon)}")
        sy_c, ry_c = score(yon, base, fc=fc)
        sy_f, ry_f = score(yon, fitted, fc=fc)
        print(f"  current {sy_c:+.3f}   fitted {sy_f:+.3f}")
        report_refusal('Yongala', ry_c + ry_f, len(yon) * 2)

    if seed_refused or loyo_refused:
        print(f"\n  !! {STIRLAG_MISSING_NOTE}")
