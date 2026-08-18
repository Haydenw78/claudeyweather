"""
Paired comparison. Both variants are scored on the SAME held-out year, so the
year-to-year noise cancels and only the difference between them is tested.
"""
import statistics, math, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # forecast_client.py lives at repo root
from forecast_client import ForecastEngine, row_from_export, STIRLAG_MISSING_NOTE
from fit_util import load
from stats import spearman

nsi = load('North Stradbroke Island')

def make(fc, useGust=False, **kw):
    """Returns a scorer f(r) -> predicted ceiling, or None if r has no
    stirLag. Refusal is per-record and counted by the caller, not
    substituted with a proxy."""
    def f(r):
        fs = row_from_export(r)
        if fs is None:
            return None
        features, spot = fs
        features['mixKmh'] = (r.get('gustKmh') if useGust else r.get('windKmh')) or 0.0
        weights = dict(fc.constants()['WEIGHTS'], **kw)
        return fc.predict(features, spot, weights=weights)['offshoreCeiling']
    return f

def med(a):
    b=sorted(a); n=len(b); return b[n//2] if n%2 else (b[n//2-1]+b[n//2])/2

def per_year(recs,f):
    out={}
    refused=0
    for y in sorted({r['year'] for r in recs}):
        train=[r for r in recs if r['year']!=y]; test=[r for r in recs if r['year']==y]
        if len(test)<8: continue
        bm=collections.defaultdict(list); bp=collections.defaultdict(list)
        for r in train:
            v=f(r)
            if v is None: refused+=1; continue
            bm[r['month']].append(r['visObservedM']); bp[r['month']].append(v)
        vm={m:med(v) for m,v in bm.items()}; pm={m:med(v) for m,v in bp.items()}
        po,oo=[],[]
        for r in test:
            v=f(r)
            if v is None: refused+=1; continue
            po.append(v-pm.get(r['month'],0)); oo.append(r['visObservedM']-vm.get(r['month'],0))
        if len(po)<4: continue
        s=spearman(po,oo)
        if s==s: out[y]=s
    return out, refused

with ForecastEngine() as fc:
    A, rA = per_year(nsi, make(fc))                      # mean wind
    B, rB = per_year(nsi, make(fc, useGust=True))          # gust
    C, rC = per_year(nsi, make(fc, useGust=True, wEkman=18))
    total_refused = rA + rB + rC
    years=sorted(set(A)&set(B)&set(C))
    print("Per held-out year, de-seasonalised Spearman\n")
    print("  year   mean-wind    gust    gust+ekman18")
    for y in years:
        print(f"  {y}     {A[y]:+.3f}    {B[y]:+.3f}     {C[y]:+.3f}")

    def paired(x,y,label):
        d=[y[k]-x[k] for k in years]
        if len(d)<2:
            print(f"\n  {label}\n    no comparable years, nothing to compare")
            return
        m=statistics.mean(d); se=statistics.stdev(d)/math.sqrt(len(d))
        t=m/se if se else 0
        print(f"\n  {label}")
        print(f"    mean difference {m:+.3f}, se {se:.3f}, t={t:.2f}, better in {sum(1 for v in d if v>0)}/{len(d)} years")
        print(f"    {'significant' if abs(t)>2.13 else 'not significant'} at 95% with {len(d)-1} df")

    if not years:
        print("\n  no years had enough scored records to compare (see refusal count below)")
    paired(A,B,'gust instead of mean wind')
    paired(A,C,'gust plus heavier ekman')
    paired(B,C,'heavier ekman on top of gust')

    if total_refused:
        print(f"\n  !! refused {total_refused} record-evaluations (no stirLag). {STIRLAG_MISSING_NOTE}")
