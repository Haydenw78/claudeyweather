"""Change one thing at a time.

Historical exploration, kept for reference only. The 42h/sd26 Ekman centre
used as the baseline below is a stale pre-revision value, not what shipped -
production settled on 72h/sd38 (see index.html's computeVis comment), which
is not one of the variants tried here either.
"""
import json, math, statistics, collections
from stats import spearman
d=json.load(open('conditions.json'))
st=next(s for s in d['stations'] if 'Stradbroke' in s['station'])
S=[x for x in st['samples'] if sum(1 for v in x['ws'] if v is not None)>220]
for x in S: x['month']=int(x['ts'][5:7]); x['year']=int(x['ts'][:4])
nor=lambda g: math.cos(math.radians(g))
def gek(x,c,sd,lo,hi):
    ws,wd=x['ws'],x['wd']; n=len(ws); a=b=0.0
    for h in range(lo,hi+1):
        i=n-1-h
        if i<0 or ws[i] is None or wd[i] is None: continue
        w=math.exp(-((h-c)/sd)**2); a+=w*min(1.0,(ws[i]/40.0)**2)*nor(wd[i]); b+=w
    return a/b if b else 0.0
def msp(x,h):
    v=[q for q in x['ws'][-h:] if q is not None]; return statistics.mean(v) if v else 0.0
def gustnow(x):
    g=[q for q in x['gs'][-1:] if q is not None]; return g[0] if g else msp(x,1)
def mix(k,on=11,full=34,e=1.15): return min(1.0,(max(0.0,k-on)/full)**e)
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
        r=spearman([f(x)-vp.get(x['month'],0) for x in te],[x['obs']-vo.get(x['month'],0) for x in te])
        if r==r: out[y]=r
    return out
def cmp(name,f,base):
    A,B=base,loyo(f); ys=sorted(set(A)&set(B))
    dif=[B[y]-A[y] for y in ys]; m=statistics.mean(dif)
    se=statistics.stdev(dif)/math.sqrt(len(dif)) if len(dif)>1 else 1
    print(f"  {name:44s} {statistics.mean(B[y] for y in ys):+.3f}  {m:+.3f}  t={m/se:+5.2f}  {sum(1 for v in dif if v>0)}/{len(dif)}")
    return B

stale_pre_revision=lambda x: 76 - 22*mix(gustnow(x)) - 10*gek(x,42,26,6,84)
base=loyo(stale_pre_revision)
print(f"  {'variant':44s} {'score':>6}  {'diff':>6}\n")
print(f"  {'baseline (stale pre-revision): gust now, ekman 42 h':44s} {statistics.mean(base.values()):+.3f}")
print()
cmp("speed: 6 h mean instead of gust",      lambda x: 76-22*mix(msp(x,6))   -10*gek(x,42,26,6,84), base)
cmp("speed: 12 h mean",                     lambda x: 76-22*mix(msp(x,12))  -10*gek(x,42,26,6,84), base)
cmp("speed: 3 h mean",                      lambda x: 76-22*mix(msp(x,3))   -10*gek(x,42,26,6,84), base)
print()
cmp("ekman centred 72 h",                   lambda x: 76-22*mix(gustnow(x)) -10*gek(x,72,30,12,140), base)
cmp("ekman centred 108 h",                  lambda x: 76-22*mix(gustnow(x)) -10*gek(x,108,44,12,200), base)
cmp("ekman centred 108 h, weight 16",       lambda x: 76-22*mix(gustnow(x)) -16*gek(x,108,44,12,200), base)
cmp("ekman centred 108 h, weight 6",        lambda x: 76-22*mix(gustnow(x)) - 6*gek(x,108,44,12,200), base)
print()
cmp("ekman off entirely",                   lambda x: 76-22*mix(gustnow(x)), base)
cmp("gust only, no ekman, weight 30",       lambda x: 76-30*mix(gustnow(x)), base)
