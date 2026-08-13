"""Look for a driver we do not already have, rather than retuning the ones we do."""
import math, statistics, collections
from fit_util import load
from stats import spearman, deseason, crit

nsi = load('North Stradbroke Island')
n = len(nsi); c = crit(n)
months = [r['month'] for r in nsi]
obs_ds = deseason([r['visObservedM'] for r in nsi], months)

def test(name, f):
    vals, keep = [], []
    for i, r in enumerate(nsi):
        v = f(r)
        if v is None: continue
        vals.append(v); keep.append(obs_ds[i])
    if len(vals) < 30: return None
    rho = spearman(deseason(vals, [nsi[i]['month'] for i in range(len(nsi)) if f(nsi[i]) is not None]), keep)
    return (name, rho, len(vals))

cands = [
    ('wind speed',            lambda r: r.get('windKmh')),
    ('gust',                  lambda r: r.get('gustKmh')),
    ('gustiness (gust/mean)', lambda r: (r['gustKmh']/r['windKmh']) if r.get('windKmh') else None),
    ('gust minus mean',       lambda r: (r['gustKmh']-r['windKmh']) if r.get('gustKmh') is not None else None),
    ('wind energy (v^2)',     lambda r: (r['windKmh']**2) if r.get('windKmh') else None),
    ('wind cubed',            lambda r: (r['windKmh']**3) if r.get('windKmh') else None),
    ('northerly component',   lambda r: math.cos(math.radians(r['windDir'])) if r.get('windDir') is not None else None),
    ('onshore (from ENE)',    lambda r: math.cos(math.radians(r['windDir']-67)) if r.get('windDir') is not None else None),
    ('alongshore (from NNE)', lambda r: math.cos(math.radians(r['windDir']-22)) if r.get('windDir') is not None else None),
    ('ekman index',           lambda r: r.get('ekman')),
    ('ekman x wind',          lambda r: (r['ekman']*r['windKmh']) if r.get('ekman') is not None else None),
    ('rain 72 h',             lambda r: r.get('rain72mm')),
    ('log rain',              lambda r: math.log1p(r.get('rain72mm') or 0)),
    ('rain over 10 mm',       lambda r: 1.0 if (r.get('rain72mm') or 0) > 10 else 0.0),
]
print(f"Candidate drivers against within-month clarity, n={n}, significant beyond ±{c:.2f}\n")
out = [test(*x) for x in cands]
out = [o for o in out if o]
for name, rho, k in sorted(out, key=lambda t: -abs(t[1])):
    mark = ' <-- real' if abs(rho) > c else ''
    print(f"  {name:24s} {rho:+.3f}  n={k}{mark}")
