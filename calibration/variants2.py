"""
Honest hold-out. De-seasonalise using monthly medians learned on the TRAINING
years only, then score the held-out year. Previously the residuals were computed
within the test year, where each month appears once, so everything collapsed
to zero and every variant scored identically.
"""
import statistics, math, collections
from engine import DEFAULTS, wind_mix
from fit_util import load
from stats import spearman, crit

nsi = load('North Stradbroke Island')

def make(useGust=False, **kw):
    p = dict(DEFAULTS, **kw)
    def f(r):
        v = (r.get('gustKmh') if useGust else r.get('windKmh')) or 0.0
        stir = min(1.6, (r.get('ubMs') or 0.0)/0.35)
        return max(0.0, min(100.0,
            p['base'] - p['wStir']*stir - p['wWind']*wind_mix(v, p)
            - p['wEkman']*(r.get('ekman') or 0.0)
            + p['wSst']*max(-2,min(2,r.get('sstAnom') or 0.0))
            - min(p['rainCap'], (r.get('rain72mm') or 0.0)*p['wRain'])))
    return f

def med(a):
    b=sorted(a); n=len(b)
    return b[n//2] if n%2 else (b[n//2-1]+b[n//2])/2

def loyo(recs, f):
    years = sorted({r['year'] for r in recs}); out=[]
    for y in years:
        train=[r for r in recs if r['year']!=y]
        test =[r for r in recs if r['year']==y]
        if len(test)<8: continue
        # climatology from training years only
        bm=collections.defaultdict(list)
        for r in train: bm[r['month']].append(r['visObservedM'])
        vm={m:med(v) for m,v in bm.items()}
        bp=collections.defaultdict(list)
        for r in train: bp[r['month']].append(f(r))
        pm={m:med(v) for m,v in bp.items()}
        po=[f(r)-pm.get(r['month'],0) for r in test]
        oo=[r['visObservedM']-vm.get(r['month'],0) for r in test]
        s=spearman(po,oo)
        if s==s: out.append(s)
    return statistics.mean(out), statistics.stdev(out)/math.sqrt(len(out)), len(out)

variants = [
    ('current (mean wind)',        make()),
    ('gust instead of mean',       make(useGust=True)),
    ('gust, no ekman',             make(useGust=True, wEkman=0)),
    ('gust, no rain',              make(useGust=True, wRain=0)),
    ('gust, ekman 18',             make(useGust=True, wEkman=18)),
    ('gust, wWind 32',             make(useGust=True, wWind=32)),
    ('gust, windFull 22 exp 0.7',  make(useGust=True, windFull=22, windExp=0.7)),
    ('wind only, nothing else',    make(wStir=0, wEkman=0, wSst=0, wRain=0)),
]
print("Leave-one-year-out, climatology learned on training years only\n")
print(f"  {'variant':30s} {'mean rho':>9s} {'se':>7s}")
res=[]
for name,f in variants:
    m,se,k=loyo(nsi,f); res.append((name,m,se))
    print(f"  {name:30s} {m:+.3f}   {se:.3f}")
best=max(res,key=lambda t:t[1]); cur=res[0]
print(f"\n  best: {best[0]} at {best[1]:+.3f}")
print(f"  gain over current: {best[1]-cur[1]:+.3f}, against a standard error of {best[2]:.3f}")
print("  " + ("real improvement" if best[1]-cur[1] > 2*best[2] else "inside the noise, not worth shipping"))
