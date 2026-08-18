# HANDOVER-5

Corrections to the project state, recorded here rather than in HANDOVER-4.md
because HANDOVER-4 is superseded and should not be edited further. This is
not a rewrite of HANDOVER-4 - it records only what has changed since it.

---

## Queensland CKAN resource catalogue, checked 2026-08-18

Tweed Heads has a separate historical-current resource:

- resource ID `bd8a6ec4-19bc-4557-8696-ce46ad307845`
- coverage: 1 July 2017 through 31 December 2018

No separate historical-current resource was found in the current catalogues
for Tweed Offshore, Bilinga, Palm Beach or Gold Coast.

## Current-speed unit ratio: the median, not the mean, is the trustworthy figure

Where a graph/feed current-speed conversion ratio has been cited: the median
ratio across the sample is approximately **1.94375**, consistent with knots
per metre/second (1 kn = 0.514444 m/s, so 1/0.514444 = 1.94384). A previously
cited mean of 2.0018 is distorted by rounded low-speed denominators - a
near-zero true speed rounded in the source data inflates a ratio computed by
division far more than it should. Use the median, and do not treat the mean as
the better-behaved statistic here.

This does not confirm the unit for the Queensland near-real-time feed's
`Current Speed` column specifically - that source states no unit at all and is
captured and reported as `unstated` throughout `qld_nrt_capture.py`. This note
records a ratio observed elsewhere, not a unit assertion for that feed.
