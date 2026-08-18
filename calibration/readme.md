# Visibility model calibration harness

Fits and tests the visibility engine offline, so the web app only has to be
rebuilt once a change has actually earned its place.

`data.json` here is a copy of `observations-all-stations.json`. That file is
the app's reference store, and which records are in it is decided by which
weather backfills happened to succeed, not by any sampling design: Port
Hacking has zero records, Yongala is missing 2017, Maria Island is missing
2024, and every station stops short of 2026. It is a display cache, not an
analytical corpus. Anything fitted on `data.json` inherits that selection.

## Data included

| file | what it is |
|---|---|
| `observations-all-stations.json` | 680 reference readings across four IMOS stations, with every driver the engine computed. The main dataset. |
| `observations-first-export.json` | Earlier export, North Stradbroke only. Kept because the lag analysis was run against it. |
| `conditions.json` | 10 days of hourly wind behind each reading. Needed by `lags.py`, which cannot work from point-in-time records. |

Scripts expect these under their original names, so `fit_util.py` looks for
`data.json`. Rename or symlink as needed.

## Workflow

1. In the app, backfill reference data and log dives.
2. Export JSON. Send the file.
3. Drop it in here as `data.json` and run the scripts below.
4. Only then patch `capricorn-window.html`.

## Scripts

| file | what it does |
|---|---|
| `engine.py` | Python port of the ceiling formula. Must stay in step with the JS. |
| `stats.py` | Spearman, rank, de-seasonalising, significance thresholds. |
| `features.py` | Ranks candidate drivers, including ones not yet in the model. |
| `fit.py` | Coordinate search over the weights with k-fold cross-validation. |
| `verify.py` | Checks a fit across seeds, by year, and on a held-out station. |
| `variants2.py` | Leave-one-year-out with climatology learned on training years only. |
| `paired.py` | Paired year-by-year comparison of two variants. The decisive test. |

## Traps already hit, do not repeat

- **De-seasonalising inside the test fold.** With one record per month per year,
  residuals collapse to zero and every variant scores identically. Learn the
  monthly climatology on the training years only.
- **Judging significance on dataset size rather than on how many records that
  driver actually had.** A correlation of +0.52 on four records is nothing.
- **Trusting an in-sample gain.** The first fit improved in-sample and got worse
  on an independent station. Cross-validate before believing anything.

## Current state

North Stradbroke, 173 readings 2009-2025, 57 m depth:

- Gust speed is the strongest available driver, rho -0.30 within-month.
- Mean wind speed close behind at -0.28.
- The lagged upwelling index adds only -0.12 once wind is controlled for.
- Instantaneous wind direction does nothing at all.
- Rain over 72 h does nothing at this site.
- Season is worth 0.32 on its own.
- Roughly 80% of variance is unexplained, some of it plankton and some of it
  Secchi reading error.

Untested: bed resuspension, because the station is 57 m deep and nothing reaches
the bottom. That term matters most at the 5-25 m sites and needs dive logs.

## Lag structure, tested August 2026

With 10 days of hourly wind behind each of 173 readings:

| window | 6h | 12h | 24h | 48h | 72h | 120h | 168h |
|---|---|---|---|---|---|---|---|
| mean wind speed | **-0.32** | -0.28 | -0.19 | -0.20 | -0.22 | -0.13 | -0.05 |
| northerly component | -0.03 | -0.04 | -0.11 | -0.15 | -0.20 | **-0.23** | -0.14 |

Two mechanisms on different clocks. Speed mixes the column within hours.
Direction changes the water mass and takes days. Both survive when the other is
held constant (-0.29 and -0.18).

Held out by year, no lag centre between 42 and 120 h beats another. All |t|
under 0.8 against se 0.05. The Ekman centre moved from 42 h to 72 h on physical
grounds, not because the data forced it.

`lags.py` sweeps the windows. `combine.py` tests independence. `isolate.py`
changes one thing at a time. `newmodel.py` shows what happens when you change
two at once: significantly worse, t=-2.14.

## Resuspension: still not settled, but no longer untestable

Four stations backfilled, 680 readings, 68 with wave data.

| station | depth | records | with waves | max bed stress | wave periods |
|---|---|---|---|---|---|
| Yongala | 26 m | 175 | 49 | 0.021 m/s | 3.0-5.4 s |
| North Stradbroke | 57 m | 173 | 4 | 0.012 m/s | 6.5-9.0 s |
| Maria Island | 85 m | 171 | 15 | 0.051 m/s | 7.0-12.6 s |
| Rottnest | 50 m | 172 | 40 | **0.336 m/s** | 8.3-14.8 s |

Sand starts moving around 0.35 m/s. The highest value anywhere in the network
is 0.051, a factor of seven short. Bed stress correlates +0.01 with clarity,
which is what a near-constant variable does.

This is not a weak result, it is an untestable one. The stations are either too
deep or sit somewhere swell cannot reach. Nine Mile at 10 m in a 1.5 m 11 s
swell sees 0.68 m/s, thirteen times anything measured here.

Pooled across the 68, station-normalised:

| driver | alone | with wind held |
|---|---|---|
| wind speed | -0.49 | - |
| sea height | -0.35 | -0.17 |
| swell height | -0.34 | -0.24 |
| rain 72 h | -0.27 | -0.18 |
| bed stress | +0.01 | - |

Sea height is largely wind in disguise (they overlap +0.43). Swell height keeps
about two thirds of its effect and is the only wave term that survives, which
points at a real swell mechanism the bed-stress formula is not capturing at
these depths.

**Do not backfill more reference stations for this.** The wave archive reaches
back only a couple of years and coverage is patchy. Dive logs at 5-10 m sites
are the only route.


### Rottnest, second attempt

The first two Rottnest runs returned nothing because of an HTTP 429 rate limit,
not missing data. The code kept trying after the first 429, which made it worse.
It now stops on the first one.

With the archive actually reachable, Rottnest gives 40 readings, bed stress to
0.336 m/s, wave periods 8.3-14.8 s. Ten times anything else in the network and
the only station approaching the 0.35 threshold.

Pooled to 108 records, normalised within both station and season:

| driver | rho |
|---|---|
| wind speed | -0.23 * |
| sea height | -0.22 * |
| swell height | -0.22 * |
| bed stress | -0.11 |
| rain 72 h | -0.08 |

Bed stress as a straight line still shows nothing. But split at 0.20 m/s:

- below: n=100, median normalised clarity 0.50
- at or above: n=8, median 0.25

A permutation test puts that at p=0.086. Suggestive, wrong side of the line,
and resting on eight records. The shape is what a threshold process should look
like, which is why a linear correlation was always the wrong test for it.

Rain, which looked significant when only station was controlled for, drops to
-0.08 once season is removed as well. It was seasonality.

**Where it stands.** Not proven, not refuted, and no longer for want of trying.
Nine Mile at 10 m in a 1.5 m 11 s swell sees 0.68 m/s, twice Rottnest's maximum.
Fifteen dive logs there would carry more weight than another reference station.
