"""
Compare the current lag structure against the one the data points at,
held out by year with climatology learned on training years only.
"""
import json, math, statistics, collections
from stats import spearman

d=json.load(open('conditions.json'))
st=next(s for s in d['stations'] if 'Stradbroke' in s['station'])
S=[x for x in st['samples'] if sum(1 for v in x['ws'] if v is not None)>220]
for x in S: x['month']=int(x['ts'][5:7]); x['year']=int(x['ts'][:4])
nor=lambda deg: math.cos(math.radians(deg))

def gauss_ekman(x, centre, sd, lo, hi):
    ws,wd=x['ws'],x['wd']; n=len(ws); num=0.0; den=0.0
    for h in range(lo,hi+1):
        i=n-1-h
        if i<0 or ws[i] is None or wd[i] is None: continue
        w=math.exp(-((h-centre)/sd)**2)
        num+=w*min(1.0,(ws[i]/40.0)**2)*nor(wd[i]); den+=w
    return num/den if den else 0.0

def mean_spd(x,h):
    v=[q for q in x['ws'][-h:] if q is not None]
    return statistics.mean(v) if v else 0.0

def mix(k,on=11,full=34,e=1.15):
    return min(1.0,(max(0.0,k-on)/full)**e)

def current(x):
    # gust at the reading, ekman centred 42 h
    g=[q for q in x['gs'][-1:] if q is not None]
    gust=g[0] if g else mean_spd(x,1)
    return 76 - 22*mix(gust) - 10*gauss_ekman(x,42,26,6,84)

def revised(x):
    # mean speed over the last 6 h, ekman centred 108 h
    return 76 - 22*mix(mean_spd(x,6)) - 16*gauss_ekman(x,108,44,12,200)

def med(a):
    b=sorted(a); n=len(b); return b[n//2] if n%2 else (b[n//2-1]+b[n//2])/2

def loyo(f):
    out={}
    for y in sorted({x['year'] for x in S}):
        tr=[x for x in S if x['year']!=y]; te=[x for x in S if x['year']==y]
        if len(te)<8: continue
        bo=collections.defaultdict(list); bp=collections.defaultdict(list)
        for x in tr: bo[x['month']].append(x['obs']); bp[x['month']].append(f(x))
        vo={m:med(v) for m,v in bo.items()}; vp={m:med(v) for m,v in bp.items()}
        r=spearman([f(x)-vp.get(x['month'],0) for x in te],
                   [x['obs']-vo.get(x['month'],0) for x in te])
        if r==r: out[y]=r
    return out

A,B=loyo(current),loyo(revised)
ys=sorted(set(A)&set(B))
print("Held-out year, de-seasonalised Spearman\n")
print("  year   current   revised    diff")
for y in ys: print(f"  {y}    {A[y]:+.3f}    {B[y]:+.3f}   {B[y]-A[y]:+.3f}")
diff=[B[y]-A[y] for y in ys]
m=statistics.mean(diff); se=statistics.stdev(diff)/math.sqrt(len(diff))
print(f"\n  current mean {statistics.mean(A[y] for y in ys):+.3f}")
print(f"  revised mean {statistics.mean(B[y] for y in ys):+.3f}")
print(f"  difference   {m:+.3f}  se {se:.3f}  t={m/se:.2f}  better in {sum(1 for v in diff if v>0)}/{len(diff)} years")
print(f"  {'SIGNIFICANT' if abs(m/se)>2.13 else 'not significant'} at 95%, {len(diff)-1} df")
