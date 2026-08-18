"""
Honest hold-out. De-seasonalise using monthly medians learned on the TRAINING
years only, then score the held-out year. Previously the residuals were computed
within the test year, where each month appears once, so everything collapsed
to zero and every variant scored identically.
"""
import statistics, math, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # forecast_client.py lives at repo root
from forecast_client import ForecastEngine, row_from_export, STIRLAG_MISSING_NOTE
from fit_util import load
from stats import spearman, crit

nsi = load('North Stradbroke Island')

def make(fc, useGust=False, **kw):
    """Returns a scorer f(r) -> predicted ceiling, or None if r has no
    stirLag. Refusal is per-record and counted by the caller, not
    substituted with a proxy."""
    weights = dict(fc.constants()['WEIGHTS'], **kw)
    def f(r):
        fs = row_from_export(r)
        if fs is None:
            return None
        features, spot = fs
        features['mixKmh'] = (r.get('gustKmh') if useGust else r.get('windKmh')) or 0.0
        return fc.predict(features, spot, weights=weights)['offshoreCeiling']
    return f

def med(a):
    b=sorted(a); n=len(b)
    return b[n//2] if n%2 else (b[n//2-1]+b[n//2])/2

def loyo(recs, f):
    years = sorted({r['year'] for r in recs}); out=[]
    refused = 0
    for y in years:
        train=[r for r in recs if r['year']!=y]
        test =[r for r in recs if r['year']==y]
        if len(test)<8: continue
        # climatology from training years only
        bm=collections.defaultdict(list)
        for r in train: bm[r['month']].append(r['visObservedM'])
        vm={m:med(v) for m,v in bm.items()}
        bp=collections.defaultdict(list)
        for r in train:
            v=f(r)
            if v is None: refused+=1; continue
            bp[r['month']].append(v)
        pm={m:med(v) for m,v in bp.items()}
        po,oo=[],[]
        for r in test:
            v=f(r)
            if v is None: refused+=1; continue
            po.append(v-pm.get(r['month'],0)); oo.append(r['visObservedM']-vm.get(r['month'],0))
        if len(po)<4: continue
        s=spearman(po,oo)
        if s==s: out.append(s)
    if not out:
        return float('nan'), float('nan'), 0, refused
    se = statistics.stdev(out)/math.sqrt(len(out)) if len(out) > 1 else float('nan')
    return statistics.mean(out), se, len(out), refused

with ForecastEngine() as fc:
    # 'baseline' here is mean wind, pre-gust - it is NOT what shipped. The app
    # has used gust (mixKmh from b.gust, falling back to b.kt) since before
    # this file was written; 'gust instead of mean' below is the variant
    # closest to production, not this one.
    variants = [
        ('baseline (mean wind, pre-gust)', make(fc)),
        ('gust instead of mean',       make(fc, useGust=True)),
        ('gust, no ekman',             make(fc, useGust=True, wEkman=0)),
        ('gust, no rain',              make(fc, useGust=True, rainPerMm=0)),
        ('gust, ekman 18',             make(fc, useGust=True, wEkman=18)),
        ('gust, wWind 32',             make(fc, useGust=True, wWind=32)),
        ('gust, windDen 22 exp 0.7',   make(fc, useGust=True, windDen=22, windExp=0.7)),
        ('wind only, nothing else',    make(fc, wStir=0, wEkman=0, wSst=0, rainPerMm=0)),
    ]
    print("Leave-one-year-out, climatology learned on training years only\n")
    print(f"  {'variant':30s} {'mean rho':>9s} {'se':>7s}")
    res=[]
    total_refused=0
    for name,f in variants:
        m,se,k,refused=loyo(nsi,f); res.append((name,m,se)); total_refused+=refused
        print(f"  {name:30s} {m:+.3f}   {se:.3f}")
    valid = [r for r in res if r[1] == r[1]]  # exclude nan
    if valid:
        best=max(valid,key=lambda t:t[1]); baseline=res[0]
        print(f"\n  best: {best[0]} at {best[1]:+.3f}")
        if baseline[1] == baseline[1]:
            print(f"  gain over baseline: {best[1]-baseline[1]:+.3f}, against a standard error of {best[2]:.3f}")
            print("  " + ("real improvement" if best[1]-baseline[1] > 2*best[2] else "inside the noise, not worth shipping"))
    else:
        print("\n  no variant scored a single held-out year (see refusal count below)")

    if total_refused:
        print(f"\n  !! refused {total_refused} record-evaluations across all variants (no stirLag). {STIRLAG_MISSING_NOTE}")
