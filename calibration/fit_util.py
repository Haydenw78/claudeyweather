import json, statistics, random
from forecast_client import ForecastEngine, rows_from_export, STIRLAG_MISSING_NOTE
from stats import spearman, deseason

_d = json.load(open('data.json'))
_ref = _d['reference']

def load(station):
    rs = [r for r in _ref if r['station'] == station]
    for r in rs:
        r['month'] = int(r['ts'][5:7]); r['year'] = int(r['ts'][:4])
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
    obs = [r['visObservedM'] for r in kept]
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

def report_refusal(label, refused, total):
    """Loud, impossible-to-miss report. A refused=N/N result with a NaN
    score is the correct output when no record carries stirLag - it is not
    a broken script."""
    if refused:
        print(f"  !! {label}: refused {refused}/{total} records (no stirLag). {STIRLAG_MISSING_NOTE}")
