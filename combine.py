"""Are the fast speed effect and the slow direction effect independent?"""
import json, math, statistics
from stats import spearman, deseason, crit, rank, pearson

d=json.load(open('conditions.json'))
st=next(s for s in d['stations'] if 'Stradbroke' in s['station'])
S=[x for x in st['samples'] if sum(1 for v in x['ws'] if v is not None)>220]
months=[int(x['ts'][5:7]) for x in S]
obs=deseason([x['obs'] for x in S],months)
c=crit(len(S))
nor=lambda deg: math.cos(math.radians(deg))

def mean_spd(x,h):
    v=[q for q in x['ws'][-h:] if q is not None]; return statistics.mean(v) if v else None
def ek(x,h):
    ws=x['ws'][-h:]; wd=x['wd'][-h:]
    p=[(ws[i],wd[i]) for i in range(len(ws)) if ws[i] is not None and wd[i] is not None]
    return sum(a*a*nor(b) for a,b in p)/len(p) if p else None

fast=deseason([mean_spd(x,6)   for x in S],months)
slow=deseason([ek(x,120)       for x in S],months)

def partial(x,y,z):
    rxy,rxz,ryz=spearman(x,y),spearman(x,z),spearman(y,z)
    den=math.sqrt((1-rxz**2)*(1-ryz**2))
    return (rxy-rxz*ryz)/den if den else None

print("fast term  = mean wind speed, last 6 h")
print("slow term  = wind-stress northerly, last 120 h\n")
print(f"  overlap between them        {spearman(fast,slow):+.2f}")
print(f"  fast vs clarity             {spearman(fast,obs):+.2f}")
print(f"  slow vs clarity             {spearman(slow,obs):+.2f}")
print(f"  fast, with slow held        {partial(fast,obs,slow):+.2f}")
print(f"  slow, with fast held        {partial(slow,obs,fast):+.2f}")
print(f"  significant beyond          ±{c:.2f}\n")

# simple combined index, searching the blend weight
rf,rs=rank(fast),rank(slow)
bestw,bestr=None,0
for w in [i/20 for i in range(21)]:
    comb=[-(w*rf[i]+(1-w)*rs[i]) for i in range(len(S))]
    r=spearman(comb,obs)
    if r>bestr: bestr,bestw=r,w
print(f"  best blend: {bestw:.2f} fast + {1-bestw:.2f} slow  ->  {bestr:+.3f}")
print(f"  fast alone {abs(spearman(fast,obs)):.3f}, slow alone {abs(spearman(slow,obs)):.3f}")
print(f"  gain over the better single term: {bestr-max(abs(spearman(fast,obs)),abs(spearman(slow,obs))):+.3f}")

# where exactly does the slow term peak
print("\n  slow term by window, finer grid:")
for h in [48,72,96,120,144,168,192]:
    v=deseason([ek(x,h) for x in S],months)
    print(f"    {h:3d} h  {spearman(v,obs):+.3f}")
