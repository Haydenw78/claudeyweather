"""
Paired comparison. Both variants are scored on the SAME held-out year, so the
year-to-year noise cancels and only the difference between them is tested.
"""
import statistics, math, collections
from engine import DEFAULTS, wind_mix
from fit_util import load
from stats import spearman

nsi = load('North Stradbroke Island')

def make(useGust=False, **kw):
    p = dict(DEFAULTS, **kw)
    def f(r):
        v = (r.get('gustKmh') if useGust else r.get('windKmh')) or 0.0
        return max(0.0, min(100.0,
            p['base'] - p['wStir']*min(1.6,(r.get('ubMs') or 0)/0.35)
            - p['wWind']*wind_mix(v,p) - p['wEkman']*(r.get('ekman') or 0)
            + p['wSst']*max(-2,min(2,r.get('sstAnom') or 0))
            - min(p['rainCap'], (r.get('rain72mm') or 0)*p['wRain'])))
    return f

def med(a):
    b=sorted(a); n=len(b); return b[n//2] if n%2 else (b[n//2-1]+b[n//2])/2

def per_year(recs,f):
    out={}
    for y in sorted({r['year'] for r in recs}):
        train=[r for r in recs if r['year']!=y]; test=[r for r in recs if r['year']==y]
        if len(test)<8: continue
        bm=collections.defaultdict(list); bp=collections.defaultdict(list)
        for r in train: bm[r['month']].append(r['visObservedM']); bp[r['month']].append(f(r))
        vm={m:med(v) for m,v in bm.items()}; pm={m:med(v) for m,v in bp.items()}
        s=spearman([f(r)-pm.get(r['month'],0) for r in test],
                   [r['visObservedM']-vm.get(r['month'],0) for r in test])
        if s==s: out[y]=s
    return out

A=per_year(nsi, make())                      # mean wind
B=per_year(nsi, make(useGust=True))          # gust
C=per_year(nsi, make(useGust=True, wEkman=18))
years=sorted(set(A)&set(B)&set(C))
print("Per held-out year, de-seasonalised Spearman\n")
print("  year   mean-wind    gust    gust+ekman18")
for y in years:
    print(f"  {y}     {A[y]:+.3f}    {B[y]:+.3f}     {C[y]:+.3f}")

def paired(x,y,label):
    d=[y[k]-x[k] for k in years]
    m=statistics.mean(d); se=statistics.stdev(d)/math.sqrt(len(d))
    t=m/se if se else 0
    print(f"\n  {label}")
    print(f"    mean difference {m:+.3f}, se {se:.3f}, t={t:.2f}, better in {sum(1 for v in d if v>0)}/{len(d)} years")
    print(f"    {'significant' if abs(t)>2.13 else 'not significant'} at 95% with {len(d)-1} df")

paired(A,B,'gust instead of mean wind')
paired(A,C,'gust plus heavier ekman')
paired(B,C,'heavier ekman on top of gust')
