"""
RETIRED. Do not import this module. It is kept in place, unimported, only so
its drift from the canonical engine stays readable rather than disappearing
with a deletion.

Originally: "Python port of the visibility engine's ceiling formula, kept
deliberately in lock-step with the JS ... so the two cannot drift." It did
not stay in lock-step. Against forecast-core.js (the actual canonical engine,
audited 2026-08-18):

  - wind_mix(): windOn=8 here vs canonical 11, and this divides by
    (windFull-windOn)=26 where canonical divides by a fixed windDen=34 -
    a different curve shape, not just different constants.
  - ceiling(): no rainReach distance-decay multiplier on the rain term at
    all, so the rain penalty here is always full-strength regardless of a
    spot's offshoreKm.
  - ceiling(): no oceanic or estuarine branch - only ever computes the
    shelf-shaped formula, unconditionally, for every record.
  - to_metres(): linear, where canonical raises the index to a calibrated
    power (2.5) before mapping onto the site's metre range.
  - The stirLag fallback here (min(1.6, ubMs/0.35)) silently substitutes a
    degenerate proxy when stirLag is absent, which was every row this was
    ever run against.

Use forecast_client.py instead, which calls forecast-core.js itself via
node_runner.js and cannot drift from the shipped app by construction.
"""
raise ImportError(
    "calibration/engine_retired_do_not_import.py is retired and must not be "
    "imported. It reimplemented the ceiling formula and drifted from "
    "forecast-core.js (see this file's module docstring for specifics). "
    "Use forecast_client.ForecastEngine instead - it calls forecast-core.js "
    "directly via node_runner.js."
)

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
