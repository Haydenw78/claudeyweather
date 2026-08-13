"""
Pooled test of the bed-resuspension term across every station with wave data.
Each station's own median is removed so they can be compared on one scale,
then season is removed within station.
"""
import json, math, statistics, collections
from stats import spearman, crit

o=json.load(open('obs3.json'))
ref=[r for r in o['reference'] if r.get('seaM') is not None]
print(f"records with wave data: {len(ref)}\n")

def norm(recs,key):
    """rank within station, so stations with different clarity can be pooled"""
    out={}
    by=collections.defaultdict(list)
    for i,r in enumerate(recs): by[r['station']].append(i)
    for st,idx in by.items():
        vals=[recs[i][key] for i in idx]
        srt=sorted(range(len(vals)),key=lambda k:vals[k])
        rk=[0]*len(vals)
        for pos,k in enumerate(srt): rk[k]=pos/(len(vals)-1) if len(vals)>1 else 0.5
        for k,i in enumerate(idx): out[i]=rk[k]
    return [out[i] for i in range(len(recs))]

vis=norm(ref,'visObservedM')
c=crit(len(ref))
print(f"pooled, station-normalised, significant beyond ±{c:.2f}\n")
print("  driver                    rho     n")
for name,key in [('bed stress (ub)','ubMs'),('sea height','seaM'),('sea period','seaS'),
                 ('swell height','swellM'),('swell period','swellS'),
                 ('wind speed','windKmh'),('rain 72 h','rain72mm')]:
    keep=[i for i,r in enumerate(ref) if r.get(key) is not None]
    if len(keep)<25: print(f"  {name:22s}  too few ({len(keep)})"); continue
    sub=[ref[i] for i in keep]
    x=norm(sub,key); y=[vis[i] for i in keep]
    r=spearman(x,y)
    star=' *' if abs(r)>crit(len(keep)) else ''
    print(f"  {name:22s} {r:+.2f}   {len(keep)}{star}")

print("\n  bed stress actually observed, by station:")
for st in sorted({r['station'] for r in ref}):
    g=[r['ubMs'] for r in ref if r['station']==st and r.get('ubMs') is not None]
    if g: print(f"    {st:26s} max {max(g):.3f} m/s   n={len(g)}   (0.35 = sand moves)")
print("\n  wave periods available:")
for st in sorted({r['station'] for r in ref}):
    g=[r['seaS'] for r in ref if r['station']==st and r.get('seaS')]
    if g: print(f"    {st:26s} {min(g):.1f}-{max(g):.1f} s, median {statistics.median(g):.1f}")
