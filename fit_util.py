import json, statistics, random
from engine import ceiling
from stats import spearman, deseason
_d = json.load(open('data.json'))
_ref = _d['reference']
def load(station):
    rs = [r for r in _ref if r['station'] == station]
    for r in rs:
        r['month'] = int(r['ts'][5:7]); r['year'] = int(r['ts'][:4])
    return rs
def score(recs, p, season=None):
    months = [r['month'] for r in recs]
    pred = [ceiling(r, p, (season or {}).get(r['month'], 0.0)) for r in recs]
    obs  = [r['visObservedM'] for r in recs]
    return spearman(deseason(pred, months), deseason(obs, months))
def kfold(recs, p, k=5, seed=1, season=None):
    rs = recs[:]; random.Random(seed).shuffle(rs)
    folds = [rs[i::k] for i in range(k)]
    out = [score(f, p, season) for f in folds if len(f) >= 12]
    return statistics.mean(out) if out else 0.0
