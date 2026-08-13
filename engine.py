"""
Python port of the visibility engine's ceiling formula.

Kept deliberately in lock-step with the JS in capricorn-window.html so the two
cannot drift. Only the terms that can be fitted from an exported observation
file live here; the lag windows that build ekman, stirLag and rain72 upstream
need raw hourly history and are not refittable from a point-in-time export.
"""
import math

DEFAULTS = dict(
    base=76.0,        # ceiling with every driver at zero
    wStir=34.0,       # weight on the 48 h settling-weighted bed stress
    wWind=22.0,       # weight on wind mixing of the column
    wEkman=10.0,      # weight on the lagged upwelling index
    wSst=6.0,         # weight on SST anomaly against the trailing 5 days
    wRain=0.9,        # per mm over 72 h
    rainCap=18.0,
    windOn=8.0,       # km/h below which wind does nothing
    windFull=34.0,    # km/h at which the mixing term saturates
    windExp=1.15,     # curvature between the two
)

def wind_mix(kmh, p):
    span = max(1e-6, p['windFull'] - p['windOn'])
    return min(1.0, (max(0.0, kmh - p['windOn']) / span) ** p['windExp'])

def ceiling(rec, p, season=0.0):
    stir = rec.get('stirLag')
    if stir is None:
        # instantaneous bed stress is the best proxy an export can give
        stir = min(1.6, (rec.get('ubMs') or 0.0) / 0.35)
    return max(0.0, min(100.0,
        p['base']
        - p['wStir'] * min(1.6, stir)
        - p['wWind'] * wind_mix(rec.get('windKmh') or 0.0, p)
        - p['wEkman'] * (rec.get('ekman') or 0.0)
        + p['wSst'] * max(-2.0, min(2.0, rec.get('sstAnom') or 0.0))
        - min(p['rainCap'], (rec.get('rain72mm') or 0.0) * p['wRain'])
        + season
    ))

def to_metres(index, vmin, vmax):
    return vmin + (vmax - vmin) * index / 100.0
