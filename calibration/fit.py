"""
Fit the ceiling weights against observed clarity, with k-fold cross-validation
so that improvements have to generalise rather than just memorise.
"""
import json, math, os, random, statistics, collections, itertools, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # forecast_client.py lives at repo root
from forecast_client import ForecastEngine, rows_from_export, STIRLAG_MISSING_NOTE
from stats import spearman, deseason, crit

d = json.load(open('data.json'))
ref = d['reference']

def load(station):
    rs = [r for r in ref if r['station'] == station]
    for r in rs:
        r['month'] = int(r['ts'][5:7])
        r['year']  = int(r['ts'][:4])
    return rs

def score(recs, p, season=None, fc=None):
    """Spearman of predicted ceiling against observed clarity, de-seasonalised.

    Refuses any record with no stirLag rather than substituting a proxy.
    Returns (rho, refused_count); rho is nan if nothing was left to score.
    """
    rows, kept, refused = rows_from_export(recs, season)
    if not kept:
        return float('nan'), refused
    preds = fc.predict_batch(rows, weights=p)
    pred = [pr['offshoreCeiling'] for pr in preds]
    obs  = [r['visObservedM'] for r in kept]
    months = [r['month'] for r in kept]
    return spearman(deseason(pred, months), deseason(obs, months)), refused

def kfold(recs, p, k=5, seed=1, season=None, fc=None):
    """Returns (mean_rho, total_refused). mean_rho is nan if no fold scored."""
    rs = recs[:]; random.Random(seed).shuffle(rs)
    folds = [rs[i::k] for i in range(k)]
    out = []
    total_refused = 0
    for i in range(k):
        test = folds[i]
        if len(test) < 12: continue
        sc, refused = score(test, p, season, fc=fc)
        total_refused += refused
        if sc == sc:  # excludes nan
            out.append(sc)
    return (statistics.mean(out) if out else float('nan')), total_refused

with ForecastEngine() as fc:
    nsi = load('North Stradbroke Island')
    print(f"North Stradbroke, n={len(nsi)}\n")

    base = dict(fc.constants()['WEIGHTS'])
    b_score, b_refused = score(nsi, base, fc=fc)
    b_kfold, b_kfold_refused = kfold(nsi, base, fc=fc)
    print(f"  current weights   in-sample {b_score:+.3f}   cross-validated {b_kfold:+.3f}")
    if b_kfold_refused:
        print(f"  !! refused {b_kfold_refused}/{len(nsi)*1} record-evaluations across folds "
              f"(no stirLag). {STIRLAG_MISSING_NOTE}")

    # coordinate search over the terms an export can actually inform
    # (names match forecast-core.js's WEIGHTS, not the retired engine.py's
    # DEFAULTS - that file called these wRain/windFull, which is itself part
    # of what drifted)
    grid = {
        'wWind':     [0, 8, 15, 22, 30, 40, 55],
        'wEkman':    [0, 5, 10, 18, 28],
        'rainPerMm': [0, 0.4, 0.9, 1.6, 2.5],
        'windOn':    [0, 4, 8, 12],
        'windDen':   [22, 28, 34, 45],
        'windExp':   [0.7, 1.0, 1.15, 1.5],
    }
    p = dict(base)
    total_search_refused = 0
    for _ in range(4):
        for k, opts in grid.items():
            best, bv = p[k], kfold(nsi, p, fc=fc)[0]
            for v in opts:
                q = dict(p); q[k] = v
                sc, refused = kfold(nsi, q, fc=fc)
                total_search_refused += refused
                if sc == sc and sc > (bv if bv == bv else -1):
                    best, bv = v, sc
            p[k] = best
    f_score, f_score_refused = score(nsi, p, fc=fc)
    f_kfold, f_kfold_refused = kfold(nsi, p, fc=fc)
    print(f"  fitted weights    in-sample {f_score:+.3f}   cross-validated {f_kfold:+.3f}")
    print("\n  fitted values:")
    for k in grid:
        if p[k] != base[k]: print(f"    {k:9s} {base[k]:>6} -> {p[k]:>6}")
        else:               print(f"    {k:9s} {base[k]:>6}    (unchanged)")

    # what does a wind-only model achieve? if it matches, the rest is decoration
    wind_only = dict(base); wind_only.update(wStir=0, wEkman=0, wSst=0, rainPerMm=0)
    wo_score, wo_refused = kfold(nsi, wind_only, fc=fc)
    print(f"\n  wind term alone   cross-validated {wo_score:+.3f}")
    null = dict(base); null.update(wStir=0, wWind=0, wEkman=0, wSst=0, rainPerMm=0)
    n_score, n_refused = kfold(nsi, null, fc=fc)
    print(f"  constant model    cross-validated {n_score:+.3f}")

    grand_total_refused = (b_kfold_refused + total_search_refused
                            + f_score_refused + f_kfold_refused + wo_refused + n_refused)
    if grand_total_refused:
        print(f"\n  !! {grand_total_refused} total record-evaluations refused across this run "
              f"(no stirLag). {STIRLAG_MISSING_NOTE}")
        print("  !! Every score and kfold value above that shows 'nan' had nothing left to "
              "score after refusal - that is the correct output, not a broken script.")
