# HANDOVER-4

Dive visibility forecasting, Gold Coast. State of the project as at 15 August 2026.

Supersedes HANDOVER-3, written the same day. Where the two disagree, this
document is correct. HANDOVER-3 was written before the historical validation
returned, before an adversarial review of the whole project, and before three
code defects were found. The places they disagree are marked, and there are
more of them than usual.

---

## 1. What the project is trying to do

Unchanged from HANDOVER-3 section 1. Predict diver visibility at Gold Coast
sites several days ahead, well enough that someone decides whether to make the
drive. The five-layer architecture stands:

1. Offshore optical state
2. Transport
3. Evolution in transit
4. Local transformation
5. Observation conditions

The strategy of establishing layers 1 to 3 on deep offshore reference stations
before touching 4 and 5 was correct and the evidence still supports it. See
section 9.

---

## 2. The scoreboard is withdrawn, not updated

HANDOVER-3 section 2 presented a "frozen scoreboard" as the model-selection
reference. **Do not use it.** Three problems, all confirmed:

- Some correlations were pooled across stations before scoring, so
  between-station structure inflated apparent skill. A site-mean-only model
  scored 0.513 pooled and near zero within site.
- Predicted tercile thresholds were derived from the held-out test batch
  itself. A live forecast does not know the distribution of future predictions.
- The permutation null shuffled within station but not within month, making it
  too easy for season-sensitive models to beat.

Its raw Kd figures also include seasonal covariance and run roughly double the
de-seasonalised values. Section 3.1 below replaces them.

A new scoreboard should not be built until the app and calibration engines are
made equivalent, because until then a coefficient can win in the harness and
behave differently in the product.

---

## 3. Established findings, corrected

### 3.1 Kd is real, and about half the size HANDOVER-3 claimed

Leave-one-out station and month de-seasonalisation, held out by calendar year,
never pooled:

| station | raw exact-day | de-seasonalised | held-out gain vs monthly climatology | shuffled p |
|---|---|---|---|---|
| Port Hacking | -0.358 | **-0.385** | 13.4% | 0.001 |
| Yongala | -0.357 | -0.306 | 9.8% | 0.001 |
| North Stradbroke | -0.310 | -0.273 | 5.4% | 0.002 |
| Maria Island | -0.255 | -0.282 | 7.0% | 0.006 |
| Rottnest | **-0.511** | -0.264 | 1.0% | 0.084 |

Kd beats a training-only monthly climatology at four of five. Mean absolute
error improves modestly: 3.03 to 2.91 m at Port Hacking, 4.29 to 4.26 m at
North Stradbroke, and it worsens at Rottnest.

**The relationship is real. It is not a metres conversion.** Do not ship one.

### 3.2 Direction transfers everywhere; magnitude transfers at three stations

Strict expanding-window validation, 11 test years, 2014 to 2024:

| station | forward skill | shuffled p95 | negative coefficient fraction | pass |
|---|---|---|---|---|
| Port Hacking | 16.24% | 1.59% | 1.00 | yes |
| Maria Island | 10.03% | 1.10% | 1.00 | yes |
| Yongala | 8.69% | 1.25% | 1.00 | yes |
| North Stradbroke | 1.38% | 1.59% | 1.00 | no, p 0.059 |
| Rottnest | 1.32% | 1.33% | 1.00 | no, p 0.052 |

Both failures are marginal, at p just above 0.05. Rottnest misses by three
parts in a hundred thousand. Report them as unresolved, not as negatives.

**The strongest single result in the project is the last column.** Every fitted
coefficient at every station in every fold had the correct sign. The folds are
dependent, so this is not dozens of independent trials, but complete
directional consistency across five stations and three coasts is portability
evidence of a kind the skill percentages do not capture.

Accurate statement: *the direction of the Kd relationship transfers across all
five stations; material prospective forecast improvement is established at
three and unresolved at two.*

### 3.3 Maria in, Rottnest out

HANDOVER-3 section 7 recommended removing Maria Island from satellite scoring
on a raw correlation of -0.147, blaming low sun angle at 42 S. **That
recommendation is withdrawn.** De-seasonalised Maria is -0.282 with 7.0%
held-out skill and it passes the forward gate. Season was masking Maria, not
carrying it. The sun-angle explanation was never demonstrated.

Rottnest is the station to demote. It has the strongest raw association in the
set and almost none survives de-seasonalisation. This coheres with two things
already on record: Rottnest is the only station where in-situ chlorophyll beat
satellite Kd, and the west coast diver transfer came out wrong-signed there.

**Rottnest now behaves anomalously in a fourth way, and it is unexplained.**
See section 5.

### 3.4 Everything else in HANDOVER-3 section 3 stands

The wind and coast-aspect result, the spatial anisotropy, the temporal
e-folding, the noise ceiling, the depth gradient, and the sea-state effect are
unchanged. Two notes:

- Section 3.5's inshore transfer result is computed at the wrong unit. See 6.3.
- **The lag centre finding of 24 to 72 hours is provisional, not established.**
  It came from exploratory searches across several windows and forms, and the
  complete search was never reproduced inside the null. Section 10 lists that
  exact failure as a live trap. Treat 24 to 72 hours as a candidate window
  until it is repeated with the unified engine and a search-aware null.

---

## 4. Code defects found and fixed

Three, all live in production before 15 August.

### 4.1 The orbital velocity solver was wrong. Fixed.

The app solved the wave dispersion relation by fixed-point iteration,
`L = L0*tanh(2*pi*d/L)`, ten passes. HANDOVER-2 already listed this as a trap
and said to use Newton. It was never done.

Measured against an exact root-find over H 0.5 to 3 m, T 4 to 18 s, d 5 to 35 m:

| | old | new |
|---|---|---|
| median relative error | 0.4% | 0 |
| p90 | 21% | |
| p95 | 37% | |
| worst | **95%** | 1.9e-15 |

Worst case H 1.2 m, T 18 s, d 5 m returned 1.67 m/s against a true 0.86 m/s.
Threshold-classification flips over a 9,889-point grid: 2.2% coarse carbonate,
1.4% shelf sand, 0.2% estuarine silt.

**The failure corner is shallow water with long-period swell, and four of the
six Tweed spots sit in it.** Fidos and Kingscliff at dMin 5 m, Tweed Bar and
Seaway Bar at 5 m. Errors skew high, so the app has been predicting bed
stirring on days with none.

Replaced with Newton on `x = kd` where `x*tanh(x) = w^2*d/g`, Guo initial
guess, six passes. Verified through a jsdom harness against an exact solver.

### 4.2 The live Kd statistic was not the validated one. Fixed.

The historical validation averaged `log(Kd)` across pixels, a geometric mean on
the original scale. The production fetcher took the arithmetic mean of raw Kd.
These differ in heterogeneous coastal water: about 2.8% on average over a
17-day sample, up to 11% on a site-day, and 17% on a synthetic field with
realistic spread.

`data/kd.json` and `data/kd_history.csv` now carry `kd` as the geometric mean
with `kd_arithmetic` alongside for audit. Any archive rows written before this
are wrong and should be rebuilt from the multi-year stream.

### 4.3 An old analysis script had Fidos 30 km out of place

It hardcoded a Fidos coordinate roughly 30 km from Nine Mile, giving about 30%
disc overlap. The true separation is 3.69 km and the overlap is 91%. Any
site-comparison output from that script is invalid. The production fetcher now
reads coordinates from `index.html`, so there is one source of truth.

Nine Mile and Fidos were also updated to plotter coordinates supplied 15
August: Nine Mile -28.19590, 153.62867 and Fidos -28.19915, 153.59147. These
supersede the values previously recorded.

---

## 5. The open validation blocker

**Same-day evidence does not validate the two-day operational term.**

The app will deliver Kd that is two days old. Almost all the validation is
same-day. On identical day-0 and day-2 casts:

| station | day-0 rho | day-2 rho | association n | **forward n** | day-2 forward skill | passes gate |
|---|---|---|---|---|---|---|
| North Stradbroke | -0.487 | -0.336 | 70-87 | **30-47** | +5.42% | no |
| Port Hacking | -0.356 | -0.129 | 70-87 | **30-47** | +2.08% | no |
| Yongala | -0.417 | -0.109 | 70-87 | **30-47** | +6.21% | no |
| Maria Island | -0.195 | -0.153 | 70-87 | **30-47** | +0.03% | no |
| **Rottnest** | -0.443 | **-0.613** | 70-87 | **30-47** | **+39.80%** | see below |

**The forward column rests on 30 to 47 predictions, not 70 to 87.** Training
history requirements remove the rest. Quote both numbers whenever this table is
reproduced, because the association and the skill are not computed on the same
sample.

This is a **validation blocker, not a stop ship.** It blocks calling the
delayed term validated. It does not block development, and it is not evidence
that two-day Kd is useless: the test uses cloud-selected raw MODIS, while the
app uses a multi-sensor gap-filled product.

Two things about this table deserve attention.

**At two stations the decay looks steeper than the field's own persistence
predicts. This is a hypothesis, not a finding.** Retained fraction of the
day-0 relationship:

| station | retained |
|---|---|
| Maria Island | 78% |
| North Stradbroke | 69% |
| Port Hacking | 36% |
| Yongala | 26% |
| Rottnest | strengthens |

Kd e-folds in 2.0 to 3.6 days, so Port Hacking and Yongala lose more than the
field alone should account for. The other three do not, so there is no general
pattern here yet. Two readings for those two stations: the day-2 raw MODIS
field is noisier than the day-0 field, which gap-filling would fix, or the
relationship decays faster than the field does. The exact Copernicus L4 test
can investigate it, and that is a second reason to run it alongside coverage.

**Rottnest is the only station that passes, and it passes backwards. Treat it
as uninformative until checked.** Day-2 stronger than day-0, with 39.8%
forward skill on roughly 30 to 47 predictions. That is the fourth way this
station has behaved unlike the others, at the one station that fails every
other test, in the one test the project most wants to pass.

Do not read the table as "one station validated the two-day term." Run a
jackknife or rank-influence check first and see whether a handful of
high-leverage casts produce it. Until then the honest summary of section 5 is
that **no station passes**, with one result pending investigation.

An earlier version of this analysis used a different cast subset at every lag,
with n varying from 95 to 117, and produced a non-monotonic curve that could
not be physical. That version is withdrawn. Requiring identical casts across
all five lags leaves 14 to 25 per station and no valid forward folds, so raw
MODIS cannot support a complete-case decay curve at all. That is a constraint
of the data, not an error by anyone.

---

## 6. Corrections to earlier analysis

### 6.1 Vis.Report counts do not reconcile

The live feed at 15 August 2026 holds **1,081 reports across 49 sites**. The
west coast analysis in HANDOVER-3 section 5 used 763. The 318-report gap is
unreconciled.

Two contaminants are visible in the feed and should be excluded explicitly:

- **Lake Leschenaultia (LES)** is a freshwater inland lake, 40 km from the coast
- **Seal Rocks (SLR)** is in Newcastle, NSW, tagged region `newcastle`

Most likely explanation for the bulk is a satellite coverage cutoff, since the
west coast work used AODN and AODN runs about 16 months behind. Checkable by
counting reports before and after the AODN end date.

Sites are still being added, so any count needs a pull date attached.

### 6.2 The Rottnest west coast rationale does not hold

HANDOVER-3 section 5 treats the Rottnest +0.144 as especially damning because
it is "the one site where the reference station is in the same water."

Rottnest is four sites on four aspects, and the composition is lopsided:

| sector | aspect | n | share |
|---|---|---|---|
| Rottnest North | north | 49 | 50% |
| Rottnest South | south | 29 | 30% |
| Rottnest East | east | 10 | 10% |
| Rottnest West | west | 10 | 10% |

The IMOS reference station sits essentially on top of Rottnest **West**, which
contributes 10 of 98 reports. Half the pooled figure is Rottnest North, roughly
13 km away on the far side of the island. The result still counts against the
transfer. The stated reason for weighting it heavily does not.

In practice "Rottnest" in that analysis means Rottnest North.

### 6.3 NRMN is pseudo-replicated at the environmental scale

HANDOVER-3 section 3.5 reports 1,731 Reef Life Survey visibility observations
and treats each as an independent case. They are not:

- 2,201 unique survey rows
- 1,731 non-null visibility readings, of which 17 are zero
- 1,714 positive-visibility surveys, but only **1,013 unique site-date clusters**
- **71.8%** of positive rows share site and date with another row
- cluster size reaches six

Kd and weather are identical within a site-date cluster. Busy survey days carry
excessive weight and uncertainty is understated. The analysis needs re-running
at one environmental unit per site-date, or with clustered inference and
grouped folds.

HANDOVER-3 also describes the 1,731 as NSW. That count is all non-null records
and includes Queensland.

### 6.4 The calibration engine is not the app engine

**Root cause, and it is written in the port's own first paragraph.** The Python
file states it is "kept deliberately in lock-step with the JS in
`capricorn-window.html`". That file no longer exists. It became `index.html`,
and the working folder still holds `capricorn-window-2.html` and
`capricorn-window-3.html` from 13 and 14 August. The port was synced to a file
that was renamed and then kept evolving, and the comment asserting lock-step is
what stopped anyone checking.

Measured over 880 cases spanning the operating range: only **144 agree**, mean
absolute difference **5.8 points** on the 0-100 index, worst **23.2**.

The review listed five mismatches. They are not of equal size.

**Rain attenuation is the dominant term.** The app scales rain by
`exp(-offshoreKm/7)`; Python applies it in full. On 60 mm over 72 hours:

| site | app subtracts | Python subtracts |
|---|---|---|
| Tweed Bar | 17.2 | 18.0 |
| Nine Mile | 7.9 | 18.0 |
| Smiths Rock | 2.8 | 18.0 |

Ten to fifteen points at the offshore sites, and most of the total gap.

**The wind mismatch largely cancels in operation.** Compared at the same km/h
the terms look far apart, 0.638 against 1.000 at 34 km/h. But the app is fed
gusts and Python sustained, and gusts run about 1.3 to 1.5 times sustained,
which roughly offsets the 11-versus-8 threshold:

| sustained km/h | app, gust x1.4 | Python |
|---|---|---|
| 12 | 0.131 | 0.116 |
| 18 | 0.366 | 0.333 |
| 25 | 0.670 | 0.613 |

Consistently biased one way, but small. Fix it, do not prioritise it.

**The metres conversion is the one that reaches the published results.** The app
uses `vMin + (vMax-vMin) * (index/100)^2.5`; Python is linear. At Fidos, vMin 2
and vMax 18:

| index | app | Python |
|---|---|---|
| 40 | 3.62 m | 8.40 m |
| 50 | 4.83 m | 10.00 m |
| 60 | 6.46 m | 11.60 m |

Better than double through the middle of the range, converging only at the ends.

**Consequence not previously drawn: every MAE figure in the validation came from
the Python side.** North Stradbroke 4.29 to 4.26 m, Port Hacking 3.03 to 2.91 m.
Those are metres under a linear conversion, and the app displays metres under a
power curve. They are not the same quantity. Rank correlations survive, because
the transform is monotonic. Anything quoted in metres does not.

**The port is partial by design, and this constrains the fix.** Its docstring
records that the lag windows building `ekman`, `stirLag` and `rain72` need raw
hourly history and cannot be refitted from a point-in-time export. Those carry
34, 10 and up to 18 points of a 100-point index, **62 combined**, so most of the
ceiling's dynamic range sits upstream of anything the port can fit. The 22-point
wind-mixing term is computed inside the engine from `windKmh`, so it is testable.

Any equivalence harness must feed both sides the same precomputed `stirLag`,
`ekman`, `rain72mm` and `sstAnom`, or it tests the lag pipeline rather than the
engine. Real rows are necessary and **not sufficient**, because they will not
exercise every branch. Synthetic fixtures are required for thresholds, caps,
missing `stirLag`, the tide gates, plume return, and the oceanic and estuarine
branches.

### 6.4.1 The calibration export cannot support calibration

The export the harness actually uses, `calibration_observations_360.json`,
holds 360 reference rows: 173 North Stradbroke and 187 Yongala, spanning 2009 to
2025. Inspection of it is worse than the missing `stirLag` alone suggests.

| field | state across 360 rows |
|---|---|
| `stirLag` | **absent entirely** |
| `ubMs` (the fallback for `stirLag`) | **zero in 324 rows, 90%** |
| `sstAnom` | zero in 326 rows, 91% |
| `swellM` / `swellS` | missing in 307 rows, 85% |
| `rain72mm` | zero in 106 rows, 29% |
| `depth` | one value per station, 57 m and 26 m |
| `type` | `shelf` for both stations |
| `observations` (user dive logs) | **0** |

**The 34-point stirring term is effectively pinned at zero.** `stirLag` is
absent so the engine falls back to `ubMs/0.35`, and `ubMs` is zero in 90% of
rows because both reference stations sit in 26 to 57 m of water where bed
orbital motion is negligible. The largest weight in the model has never moved
during any fitting run. The 6-point SST term is likewise near-constant.

**Neither reference station resembles a dive site.** Both are typed `shelf` at
26 and 57 m. Every Tweed spot is 5 to 25 m, and four of six are at dMin 5 m. The
oceanic and estuarine branches, the tide gates and the plume return have never
been exercised by real data at all.

**The orbital solver bug fed straight into this.** The `stirLag` fallback
divides `ubMs` by 0.35, and `ubMs` came from the fixed-point iteration that was
up to 95% wrong in shallow long-period swell. The error was largest exactly
where the term would have mattered.

Running the Python engine over these rows and comparing against `visPredictedM`,
which the app itself recorded in the same export, gives a Spearman of **0.367**.
That is a direct app-against-port comparison on real data, and it is not a
near-match. The residual is also uniformly negative across all twelve months, by
2.9 to 5.8 m, consistent with the app applying `seasonAdj` while the port is
given `season=0`.

**Conclusion: the harness cannot currently justify any coefficient.** Three of
six terms vary meaningfully, on two deep shelf stations, in a branch that no
Gold Coast dive site uses. This is a stronger statement than the review made and
it should be recorded as such.

**Open decision, not an engineering task: which direction should equivalence
run?** Three options with different costs.

| option | cost | note |
|---|---|---|
| Port the app to Python | high | the port covers only the shelf ceiling; the app also has oceanic and estuarine branches, tide gating, plume return and `seasonAdj` |
| One shared implementation both call | highest now, lowest later | the clean answer |
| Freeze the app as reference, treat the port as a fitting surface only | lowest | honest about what the port is, but never selects shipping coefficients |

**A coefficient can win in the harness and lose in the product.** Until this is
resolved, no scoreboard can select app coefficients. The orbital solver fix has
widened the gap further, since it is in the app only.

### 6.5 Everything in HANDOVER-3 section 6 still stands

The +/-1 day join, the depth window, the widened AODN export, and North
Stradbroke's instability across year halves. The join fix has been verified as
present in the validation run: Maria matched 153 casts of 184.

---

## 7. Tested and dead

Additions to HANDOVER-3 section 4. These were tested this round and should not
be re-proposed without new evidence.

| claim | status | evidence |
|---|---|---|
| **ZSD as an independent clarity predictor** | **dead** | Spearman -1.000 against Kd, log-linear R2 above 0.9999. A unit change, not new information. |
| **Rottnest aspect-pooling explains the west coast null** | **dead** | Rottnest West has 10 reports, none since December 2022. Underpowered, and the WA mainland coast faces west almost uniformly, so there is not enough aspect contrast for pooling to have caused it. |
| Kd subsumes the ekman and seasonAdj terms | **untested, do not assume** | Kd is a state variable at two days old; wind and season can lead it. Removing them because they correlate with Kd is the risky direction. |
| Bottom reflectance disqualifies the Capricorn cays | **not needed** | Seven of eight Capricorn spots are type `oceanic`, and the oceanic branch of the model bypasses the ceiling term entirely, so there is nowhere for Kd to enter. The optical argument was an inference of the same shape as the Maria sun-angle claim and is unproven. |

**SPM is the live candidate.** Rank correlation with Kd of about +0.57, so
substantial independent structure, and it targets the mineral-particle pathway
that matters for Darwin, river plumes, shallow sites and resuspension.

---

## 8. Infrastructure now running

**Kd nowcast, live since 15 August.** GitHub Action, daily at 20:00 UTC,
06:00 Brisbane. Writes `data/kd.json` and appends `data/kd_history.csv`, then
commits both.

**Nine spots, parsed from `index.html`** so the lists cannot drift: Seaway Wall,
Seaway Bar, Nine Mile, Fidos, Kingscliff, Tweed Bar, Flinders Reef, Smiths
Rock, Cape Moreton. Capricorn excluded per section 7.

**Two streams, recorded per row.**

| stream | dataset | coverage |
|---|---|---|
| `nrt` | `cmems_obs-oc_glo_bgc-transp_nrt_l4-gapfree-multi-4km_P1D` | **17-day rolling window only** |
| `my` | `cmems_obs-oc_glo_bgc-transp_my_l4-gapfree-multi-4km_P1D` | 1997-09-04 to present, version 202603 |

The L3 equivalents exist for both and the multi-year L3 identifier is confirmed
working.

**The NRT window is 17 days.** Any day not captured within 17 days is
unrecoverable from that stream. The archive is not a convenience.

**Caveat on the multi-year stream:** it is currently being reprocessed, and
data after 2026-02-16 may not be final. A later rerun producing slightly
different recent values is expected, not a bug.

Every row carries `product_stream`, `l3_observed_fraction`,
`l4_interpolated_fraction`, `confidence`, a site-level `caveat`
(`land_adjacency` for the four inshore and bar spots), `ocean_cells`, and
`age_days_at_fetch`.

**Site geometry.** All nine spots sit within 18 km of each other and the disc
radius is 25 km, so the discs overlap heavily. Nine Mile and Fidos share 91%
of their cells.

Most of the offshore optical contribution is therefore shared, so paired-site
differences will predominantly identify layers 4 and 5. Residual offshore
differences still exist and must still be measured: the 9% of non-overlapping
cells already produced a 7.5% gap between Nine Mile and Fidos on the first live
day, and a front can sit between two sites 3.7 km apart.

That still makes the dive log an unusually clean experiment. With the offshore
term nearly flat across a same-day Fidos and Nine Mile pair, most of the spread
you record is local signal. Not all of it.

---

## 9. On the strategy

Unchanged from HANDOVER-3 section 8 and strengthened by the review. The
offshore phase bought the observable, the additive structure, the climatology
contrast, the wind term's form, and a measured size for the baseline.

The counter-consideration is also unchanged and is now reinforced by an
independent reviewer: **the reference stations never sampled a bad day.** The
windy half tops out around 34 to 43 km/h peak and real conditions reach 90. The
app should report when conditions fall outside the calibration range rather
than emitting a confident number.

Two further sampling risks the review raised and this project had not recorded:

- **Diver visibility is heaped.** NRMN values pile up at 5, 8 and 10 m.
  Treating them as precise continuous metres overstates the resolution. Ordered
  bands or an interval observation model would be more honest.
- **Dive-day selection bias excludes the worst conditions.** Surveys happen when
  somebody chose to dive. A model trained only on completed dives is
  range-restricted exactly where "do not go" matters most. **Logging cancelled
  dives is not optional for a decision product.**

---

## 10. Traps

From HANDOVER-3 section 9, all still live. Added this round:

- **A running Python process does not pick up an edited script.** Two backfills
  were run from stale code before this was noticed.
- **Two copies of the project, one on the Mac and one on GitHub, drift
  silently.** Editing one has no effect on the other, and the same file was
  repeatedly assumed to be current in both. A git clone would remove this class
  of problem. The repo also currently lives in iCloud Drive, which evicts files
  and can damage `.git`.
- **Two processes writing the same append-only file will silently double it.**
  The duplicate guard reads at start and appends at end, so concurrent runs
  cannot see each other.
- **Verify a fix by checking its output, not its presence.** Confirm the header
  of a rebuilt file contains the new column rather than confirming the new code
  sits in the folder.
- **A control specified in a plan is not a control that was run.** The five-lag
  analysis listed complete-case matching as its first control and then reported
  results with n varying by lag.
- **State the pass gate before the numbers land**, including the case where the
  answer is "underpowered". An underpowered null is not evidence of no effect.

---

## 11. Work order

**Zero, before anything else**

0. **Establish one canonical repository and record the tested commit.** Today
   there are at least three copies of the app in play, a Mac working folder
   inside iCloud Drive, the GitHub repo, and whatever Chat reads. They have
   already drifted: a stale process ran old code through two backfills, the
   local `index.html` sat two revisions behind GitHub, and iCloud produced
   conflict copies with " 2" in the filenames. Proving engine equivalence
   against the wrong copy would be the same failure with a certificate
   attached. Clone the repo outside iCloud, work from it, and record the commit
   hash any result was produced against.

**Immediate, to make any later test interpretable**

1. ~~Fix the orbital solver.~~ Done, section 4.1.
2. ~~Make the production Kd statistic the validated one.~~ Done, section 4.2.
3. Build app and Python golden-vector equivalence. 100 to 500 fixed cases
   spanning every site and the observed range of every input, run through both,
   agreeing to 1e-6 on components and 0.01 m on display. Missing-data decisions
   must agree exactly.
4. Rebuild `kd_history.csv` from the multi-year stream and confirm the header
   carries `kd_arithmetic`.

**Next, to settle the operational question**

5. Repeat the historical analysis on the exact Copernicus multi-year L3 and L4
   products at 0, 1, 2, 3 and 5 days, on common casts, at issue-time
   availability.

   **Gate, set now.** Four predeclared models, same rows, same folds:

   | | model |
   |---|---|
   | A | training-only monthly climatology |
   | B | A + two-day-old overhead L4 Kd |
   | C | A + operational weather and season terms |
   | D | A + Kd + weather and season |

   The decisive comparisons are **B against A** and **D against C**. Kd passes
   if forward MAE improves beyond a block-shuffled p95 at three or more
   stations, with a negative coefficient in at least 75% of folds.

   Do not use "persistence" as a baseline here. In HANDOVER-3 that word means
   persisting today's Kd forward as a prediction of Kd, which is a forecast of
   the optical field rather than a competing predictor of Secchi. Reserve the
   term for that use only.
6. Check whether the Rottnest day-2 pass is driven by a few casts.
7. Predeclare four models on the operational timeline: climatology; plus last
   available Kd; plus wind and season; all three. Retire a term only if
   removing it costs under 1% forward MAE at every station.
8. Add SPM.

**Then**

9. Re-run NRMN at one unit per site-date with grouped folds.
10. Compare model forms and averaging radii inside nested forward validation.
    The 25 km disc is physically motivated and has never been selected
    prospectively, and the field is anisotropic, so an alongshore ellipse may
    beat an isotropic circle.
11. Swell direction crossed with shelter and depth at Port Stephens, sectors
    chosen from coastline geometry rather than from the outcome.

**Building the evidence that cannot be accelerated**

12. **Start the dive log.** Paired Fidos and Nine Mile the same day. Working
    depth, vis at surface against at depth, water colour, boundary depth,
    current, light, observation method, observer, and whether the bottom was
    reached.
13. Log cancelled and no-go days.
14. Capture competitor forecasts prospectively, with the scoring rule fixed in
    advance. Lower priority than it looks: forecasts cannot be scored without
    observations to score them against, so the log comes first.

---

## 12. Contacts and sources

**Patrick Morrison**, WA Museum, `patrick.morrison@museum.wa.gov.au`. Runs
Vis.Report (`patrick-morrison/visreport2`, data CC BY-SA 4.0) and wrote the 2021
hierarchical Bayesian analysis at
`padmorrison.com/posts/2021-09-05-predicting-underwater-visibility/`. Initial
contact sent 15 August 2026, no reply yet. Four years ahead on the inshore
half, no state term.

**Reef Check Australia**, `database@reefcheckaustralia.org`. Raw data requires
a signed custom data licence agreement. Enquiry sent 15 August 2026, no reply
yet. The qualifying question is whether their SEQ site description records
horizontal visibility as a number at all. The only Gold Coast survey data
identified.

**Data in use:** IMOS/AODN BGC (Secchi, chlorophyll, TSS), AODN
`imos:ep_survey_list_public_data` (Reef Life Survey), Copernicus Marine (GLORYS
currents, ocean colour NRT and multi-year), Open-Meteo archive and marine
archive, Vis.Report.

**Not yet approached:** EHMP (site groups and fields identified, request not
submitted), AIMS manta-tow and octocoral datasets (GBR oceanic water, low
priority for the Gold Coast product), Freediving Gold Coast and local charter
operators.

**Freediving Gold Coast and charter operators are the highest-value outreach on
the list.** They are the only route to diver observations at Nine Mile, Fidos
and the Tweed sites, which no public dataset covers, and the lead time on
collecting them alone is a season.

**Dead ends:** Unitywater North Pine River, bottom-limited river data, useful
only as a documented failure domain.

---

## 13. Task register

Everything open, in one place, because several processes are running in
parallel and items have been lost between them. Status as at 15 August 2026.

### Blocking

| # | task | state | notes |
|---|---|---|---|
| B1 | One canonical repository, tested commit recorded | **not started** | see section 11 item 0. Everything else waits on this |
| B2 | Decide which direction engine equivalence runs | **decision needed** | three options in 6.4. Not an engineering task |
| B3 | Extract the forecast calculation into one canonical JS module; browser imports it, Python batch-calls it via Node | blocked on B1 | option 2, agreed. Batch through stdin as JSON lines or keep one Node process alive; per-row spawning will be too slow for permutation nulls |
| B4 | Golden-vector fixtures: 360 real rows plus synthetic branch coverage | blocked on B3 | module must emit a component trace, not just an index, or two cancelling errors pass a final-number check |
| B5 | Exporters record the full calculation trace, not just the prediction | **not started** | `stirLag`, `windMix`, `rainReach`, `seasonAdj`, `ceiling` and `tideQ` are currently local variables inside `computeVis()` and are discarded after use. The extracted module already returns them |
| B6 | Rebuild real-row fixtures from the richer export | blocked on B5 | will give good shelf fixtures. Will not manufacture oceanic or estuarine data |

**The oceanic and estuarine coefficients cannot be fitted from any reference
data, ever.** The historical reference importer constructs every IMOS station as
`type: 'shelf'`, and no amount of re-exporting changes that. Synthetic fixtures
prove those branches behave identically across implementations; only genuine
observations can fit their coefficients. The same applies to `stirLag`: the
reference stations sit in 26 to 57 m, historical wave coverage is limited, and
even a perfect export may leave the 34-point stirring term unidentified.

**This makes the dive log (D1) the sole possible source for two of three
branches and for the largest single weight in the model.** It was already the
binding constraint. It is now also the only route to calibrating the estuarine
sites, which is every bar and Seaway spot in the app.

### Code and infrastructure

| # | task | state |
|---|---|---|
| C1 | Orbital solver, Newton | **done**, section 4.1 |
| C2 | Geometric mean in the fetcher | **done**, section 4.2 |
| C3 | Rebuild `kd_history.csv` from the multi-year stream | in progress |
| C4 | Confirm the rebuilt header carries `kd_arithmetic` | not started |
| C5 | Upload current `fetch_kd.py` and `index.html` to GitHub, confirm the Action uses them | not started |
| C6 | Delete iCloud conflict copies and `fetch_kd-4.py` | not started |
| C7 | App reads `data/kd.json`, surfaces `confidence` and `caveat` in the UI | not started |
| C8 | One authoritative site registry, automated coordinate and distance checks | partially done, fetcher reads `index.html` |

### Analysis

| # | task | state | notes |
|---|---|---|---|
| A1 | Copernicus MY L3/L4 at lags 0,1,2,3,5, common casts, issue-time | not started | the decisive test. Gate in section 11 item 5 |
| A2 | Jackknife the Rottnest day-2 result | not started | before it does any work, section 5 |
| A3 | Four-model ablation, climatology / +Kd / +weather / all | not started | section 11 item 7 |
| A4 | Add SPM | not started | rank correlation +0.57 with Kd |
| A5 | Re-run NRMN at site-date clusters | not started | section 6.3 |
| A6 | Reconcile Vis.Report 763 against 1,081 | not started | check the AODN cutoff first, section 6.1 |
| A7 | Model form and averaging radius inside nested forward validation | not started | 25 km never selected prospectively |
| A8 | Swell direction by shelter and depth, Port Stephens | not started | sectors from coastline geometry, not outcome |
| A9 | Repeat the lag-window search with a search-aware null | not started | 24-72 h is provisional, section 3.4 |

### Data and outreach

| # | task | state | notes |
|---|---|---|---|
| D1 | **Start the dive log** | not started | the binding constraint, cannot be accelerated |
| D2 | Log cancelled and no-go days | not started | required for a decision product |
| D3 | Freediving Gold Coast and charter operators | not contacted | highest-value outreach on the list |
| D4 | Reef Check Australia | sent 15 Aug, no reply | |
| D5 | Patrick Morrison | sent 15 Aug, no reply | |
| D6 | EHMP request | fields identified, not submitted | long lead, submit early |
| D7 | Competitor forecast capture | not started | worthless until D1 provides something to score against |
| D8 | AIMS manta-tow and octocoral | not started | GBR oceanic water, low priority for this product |

### Closed

| task | outcome |
|---|---|
| ZSD as an independent predictor | dead, Spearman -1.000 against Kd |
| Rottnest aspect-pooling hypothesis | dead, Rottnest West has 10 reports, none since Dec 2022 |
| Unitywater North Pine River | dead, bottom-limited river data |
| Lagrangian upstream sampling | dead, both rotated controls beat the treatment |
| Complete-case five-lag decay curve on raw MODIS | not possible, 14-25 casts per station |

---

## 14. The honest claim today

> Satellite optical attenuation carries real information about recent water
> clarity at Australian offshore monitoring sites. The direction of the
> relationship holds at all five stations tested. Material prospective forecast
> improvement, at same-day observation, is established at three. Whether the
> live gap-filled product at its actual two-day delivery age improves forecasts
> beyond climatology and weather alone is the next test, and on current evidence
> no station has passed it.

That is harder to knock down than anything stronger, and it is still worth
building on.
