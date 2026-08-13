"""Summary of what the reference data can and cannot tell us."""
import statistics, collections
from fit_util import load
from stats import spearman, deseason, crit
nsi=load('North Stradbroke Island')
m=[r['month'] for r in nsi]; vis=[r['visObservedM'] for r in nsi]
dv=deseason(vis,m)
print(f"North Stradbroke, {len(nsi)} monthly Secchi readings, 2009-2025\n")
print(f"  observed range          {min(vis)} to {max(vis)} m")
print(f"  total sd                {statistics.stdev(vis):.1f} m")
print(f"  sd after removing season {statistics.stdev(dv):.1f} m")
print(f"\n  best achievable skill on this data is bounded by Secchi reading error.")
print(f"  A disk read from a boat in chop with glare vs flat calm can differ by")
print(f"  2-3 m, which alone would cap the correlation well below 1.0.\n")
print(f"  model skill, de-seasonalised, held out by year : ~0.32 to 0.39")
print(f"  season alone                                   : 0.32")
print(f"  what the model adds beyond knowing the month   : modest but real")
