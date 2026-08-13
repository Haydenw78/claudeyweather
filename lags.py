"""
Does wind direction matter when you give it time to act?

Instantaneous direction showed nothing. This builds direction and speed indices
over every lookback window from 6 hours to 10 days and tests each one.
"""
import json, math, statistics, collections
from stats import spearman, deseason, crit

d = json.load(open('conditions.json'))
st = next(s for s in d['stations'] if 'Stradbroke' in s['station'])
S = [x for x in st['samples'] if sum(1 for v in x['ws'] if v is not None) > 220]
print(f"{st['station']}: {len(S)} readings with complete wind history\n")

months = [int(x['ts'][5:7]) for x in S]
obs    = deseason([x['obs'] for x in S], months)
crit_n = crit(len(S))

def window(x, hours, offset=0):
    """last `hours` of the series, ending `offset` hours before the reading"""
    n = len(x['ws']); end = n - offset
    return max(0, end - hours), end

def nor(deg): return math.cos(math.radians(deg))

def feat(x, hours, offset, kind):
    a, b = window(x, hours, offset)
    ws = [x['ws'][i] for i in range(a, b) if x['ws'][i] is not None]
    wd = [x['wd'][i] for i in range(a, b) if x['wd'][i] is not None]
    if not ws: return None
    if kind == 'meanspd':  return statistics.mean(ws)
    if kind == 'maxspd':   return max(ws)
    if kind == 'energy':   return sum(v*v for v in ws)/len(ws)
    if kind == 'north':    return statistics.mean(nor(v) for v in wd)
    # Ekman-style: wind stress goes as speed squared, signed by direction
    if kind == 'ekman':    return sum(ws[i]**2 * nor(wd[i]) for i in range(len(ws)))/len(ws)
    if kind == 'northfrac':return sum(1 for v in wd if nor(v) > 0.5)/len(wd)
    if kind == 'southfrac':return sum(1 for v in wd if nor(v) < -0.5)/len(wd)
    return None

HOURS = [6, 12, 24, 48, 72, 120, 168, 240]
KINDS = [('meanspd','mean wind speed'), ('maxspd','peak wind speed'),
         ('energy','wind energy v2'), ('north','mean northerly component'),
         ('ekman','wind-stress northerly'), ('northfrac','hours from the north'),
         ('southfrac','hours from the south')]

print(f"Spearman against within-month clarity. Significant beyond ±{crit_n:.2f}\n")
print("  driver                       " + "".join(f"{h:>7}h" for h in HOURS))
best = []
for key, label in KINDS:
    row = []
    for h in HOURS:
        v = [feat(x, h, 0, key) for x in S]
        keep = [i for i, q in enumerate(v) if q is not None]
        if len(keep) < 100: row.append(None); continue
        r = spearman(deseason([v[i] for i in keep], [months[i] for i in keep]),
                     [obs[i] for i in keep])
        row.append(r); best.append((abs(r), r, label, h))
    print(f"  {label:26s} " + "".join(
        (f"{v:+7.2f}" + ("*" if v is not None and abs(v) > crit_n else " ")) if v is not None else "      - "
        for v in row))

best.sort(reverse=True)
print("\n  strongest five:")
for a, r, label, h in best[:5]:
    print(f"    {label} over {h} h: {r:+.3f}")
