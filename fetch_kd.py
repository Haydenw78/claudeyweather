#!/usr/bin/env python3
"""
Kd490 nowcast fetcher for ClaudeyWeather.

Two modes:

  python fetch_kd.py                        nowcast: writes data/kd.json and
                                            appends the day to the archive
  python fetch_kd.py --backfill 2022-01-01  archive only: walks every available
                                            day from that date to now

Value comes from the Copernicus L4 gap-free field, which the pixel-level audit
showed preserves every paired L3 observation to floating-point precision.
Coverage accounting comes from L3, so every value carries a record of how much
of its disc was observed rather than interpolated.

Spots are read from index.html rather than duplicated here, so the two lists
cannot drift when a spot is added or moved.

Auth: run `copernicusmarine login` once, or set
COPERNICUSMARINE_SERVICE_USERNAME and COPERNICUSMARINE_SERVICE_PASSWORD.
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import copernicusmarine as cm

# --- config ---------------------------------------------------------------

# Two processing streams. NRT is the operational feed but keeps only a ~17 day
# rolling window. MY is the reprocessed archive back to 1997, and is a DIFFERENT
# processing stream, so every archive row records which one it came from. A step
# change at the join would otherwise look like a real signal.
PRODUCTS = {
    "nrt": ("cmems_obs-oc_glo_bgc-transp_nrt_l4-gapfree-multi-4km_P1D",
            "cmems_obs-oc_glo_bgc-transp_nrt_l3-multi-4km_P1D"),
    "my":  ("cmems_obs-oc_glo_bgc-transp_my_l4-gapfree-multi-4km_P1D",
            "cmems_obs-oc_glo_bgc-transp_my_l3-multi-4km_P1D"),
}
VAR = "KD490"

DISC_RADIUS_KM = 25.0
LOOKBACK_DAYS = 6              # L4 latency is ~2 days; walk back if the tail is short
SPOT_SOURCE = Path("index.html")
OUT_PATH = Path("data/kd.json")
ARCHIVE_PATH = Path("data/kd_history.csv")

# Capricorn is excluded. Seven of its eight spots are type 'oceanic', and the
# oceanic branch of the visibility model bypasses the ceiling term entirely, so
# there is nothing there for a Kd value to feed. Its cays also sit on bright
# shallow carbonate, where Kd490 retrieval is contaminated by bottom reflectance.
REGIONS = {"tweed", "moreton"}

# Confidence ladder, keyed on interpolated fraction of the disc.
FLAG_THRESHOLDS = [
    (0.75, "weak_prior"),
    (0.50, "low_confidence"),
    (0.25, "flagged"),
]

MIN_OCEAN_CELLS = 20           # below this a disc mean is not representative


def confidence(interp_frac):
    for cut, label in FLAG_THRESHOLDS:
        if interp_frac >= cut:
            return label
    return "ok"


def caveat(spot, n_ocean):
    """Retrieval caveats that belong to the site rather than to the day."""
    out = []
    if spot.get("type") == "estuarine" or spot.get("bar") or spot.get("inshore"):
        out.append("land_adjacency")
    if n_ocean < MIN_OCEAN_CELLS:
        out.append("few_ocean_cells")
    return "|".join(out) or "none"


# --- spot parsing ---------------------------------------------------------

REGION_RE = re.compile(r"\{id:'([a-z0-9]+)',\s*label:'[^']*'")
SPOT_RE = re.compile(r"\{id:'([a-z0-9]+)',\s*offshoreKm:.*?\}", re.S)


def _field(blob, name, cast=str):
    m = re.search(name + r":\s*'?(-?[\w. ]+?)'?\s*[,}]", blob)
    return cast(m.group(1)) if m else None


def parse_spots(path=SPOT_SOURCE):
    """Pull id, name, lat, lon and type for every spot in the wanted regions."""
    if not path.exists():
        sys.exit(f"Cannot find {path}. Run this from the repo root.")
    src = path.read_text()

    marks = [(m.start(), m.group(1)) for m in REGION_RE.finditer(src)]
    if not marks:
        sys.exit("No region blocks found in index.html. Has the format changed?")
    marks.append((len(src), None))

    spots = {}
    for (start, region), (end, _) in zip(marks, marks[1:]):
        if region not in REGIONS:
            continue
        for m in SPOT_RE.finditer(src[start:end]):
            blob = m.group(0)
            lat, lon = _field(blob, "lat", float), _field(blob, "lon", float)
            if lat is None or lon is None:
                continue
            flat = blob.replace(" ", "")
            spots[m.group(1)] = {
                "lat": lat, "lon": lon,
                "label": _field(blob, "name") or m.group(1),
                "type": _field(blob, "type") or "shelf",
                "bar": "bar:true" in flat,
                "inshore": "inshore:true" in flat,
                "region": region,
            }
    if not spots:
        sys.exit("Parsed zero spots. Has the spot format in index.html changed?")
    return spots


# --- geometry -------------------------------------------------------------

def disc_mask(lats, lons, site_lat, site_lon, radius_km):
    """Boolean (lat, lon) mask of cells within radius_km of the site."""
    lat_g, lon_g = np.meshgrid(lats, lons, indexing="ij")
    lat_r, lon_r = np.radians(lat_g), np.radians(lon_g)
    s_lat_r, s_lon_r = np.radians(site_lat), np.radians(site_lon)
    d = np.arccos(
        np.clip(
            np.sin(lat_r) * np.sin(s_lat_r)
            + np.cos(lat_r) * np.cos(s_lat_r) * np.cos(lon_r - s_lon_r),
            -1.0, 1.0,
        )
    )
    return (6371.0 * d) <= radius_km


def bbox(spots, pad_deg):
    lats = [s["lat"] for s in spots.values()]
    lons = [s["lon"] for s in spots.values()]
    return (min(lons) - pad_deg, max(lons) + pad_deg,
            min(lats) - pad_deg, max(lats) + pad_deg)


# --- per-day computation --------------------------------------------------

def site_values(spots, v4, l3_vals, masks):
    """Disc statistics for every spot on one day's field."""
    res = {}
    for key, spot in spots.items():
        # L4 is gap-free over ocean and NaN over land, so finite L4 defines the
        # ocean cells of the disc.
        ocean = masks[key] & np.isfinite(v4)
        n_ocean = int(ocean.sum())
        if n_ocean == 0:
            res[key] = {"label": spot["label"], "region": spot["region"],
                        "kd": None, "confidence": "no_data",
                        "caveat": caveat(spot, 0)}
            continue

        # The historical validation averaged log(Kd) across pixels, i.e. a
        # geometric mean on the original scale. Production must compute the
        # same statistic or the live feature is not the validated one. In the
        # 17-day sample the arithmetic mean ran ~2.8% high, up to ~11% on a
        # site-day, which is enough to matter near a threshold.
        vals = v4[ocean]
        kd_geom = float(np.exp(np.mean(np.log(vals))))
        row = {
            "label": spot["label"],
            "region": spot["region"],
            "kd": round(kd_geom, 5),                       # validated feature
            "kd_arithmetic": round(float(np.mean(vals)), 5),  # kept for audit
            "ocean_cells": n_ocean,
            "caveat": caveat(spot, n_ocean),
        }

        if l3_vals is None:
            # No L3 for this day. Coverage is unknown, which is NOT the same as
            # fully interpolated. Recording it as 1.0 would mark good days bad.
            row.update(l3_observed_fraction=None, l4_interpolated_fraction=None,
                       confidence="unknown")
        else:
            obs_frac = int((ocean & np.isfinite(l3_vals)).sum()) / n_ocean
            row.update(l3_observed_fraction=round(obs_frac, 3),
                       l4_interpolated_fraction=round(1.0 - obs_frac, 3),
                       confidence=confidence(1.0 - obs_frac))

        res[key] = row
    return res


def open_pair(spots, start, end, stream="nrt"):
    l4_id, l3_id = PRODUCTS[stream]
    pad = DISC_RADIUS_KM / 111.0 + 0.1
    lon_min, lon_max, lat_min, lat_max = bbox(spots, pad)
    common = dict(
        variables=[VAR],
        minimum_longitude=lon_min, maximum_longitude=lon_max,
        minimum_latitude=lat_min, maximum_latitude=lat_max,
        start_datetime=start, end_datetime=end,
        username=os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME"),
        password=os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"),
    )
    ds4 = cm.open_dataset(dataset_id=l4_id, **common)
    try:
        ds3 = cm.open_dataset(dataset_id=l3_id, **common)
    except Exception as e:
        # Coverage accounting is a nice-to-have. Losing L3 must not lose the run.
        print(f"warning: L3 unavailable ({type(e).__name__}), "
              f"observed fraction will be recorded as unknown")
        ds3 = None
    return ds4, ds3


def l3_for(ds3, time_val):
    if ds3 is None:
        return None
    try:
        return ds3[VAR].sel(time=time_val, method="nearest",
                            tolerance=np.timedelta64(1, "D")).values
    except (KeyError, IndexError):
        return None


# --- archive --------------------------------------------------------------

ARCHIVE_FIELDS = [
    "source_date", "site", "region", "kd", "kd_arithmetic", "ocean_cells",
    "l3_observed_fraction", "l4_interpolated_fraction",
    "confidence", "caveat", "product_stream", "fetched_utc", "age_days_at_fetch",
]


def read_existing():
    seen = set()
    if ARCHIVE_PATH.exists():
        with ARCHIVE_PATH.open(newline="") as fh:
            for row in csv.DictReader(fh):
                seen.add((row["source_date"], row["site"]))
    return seen


def append_rows(rows):
    """Append to the archive, never rewriting. Returns rows written."""
    if not rows:
        return 0
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not ARCHIVE_PATH.exists()
    with ARCHIVE_PATH.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ARCHIVE_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)
    return len(rows)


def rows_from(source_date, sites, fetched_utc, age_days, seen, stream="nrt"):
    return [
        dict(source_date=source_date, site=key, fetched_utc=fetched_utc,
             age_days_at_fetch=age_days, product_stream=stream, **s)
        for key, s in sites.items()
        if s.get("kd") is not None and (source_date, key) not in seen
    ]


# --- modes ----------------------------------------------------------------

def run_nowcast(spots):
    now = datetime.now(timezone.utc)
    ds4, ds3 = open_pair(spots, now - timedelta(days=LOOKBACK_DAYS), now)
    if ds4.sizes.get("time", 0) == 0:
        sys.exit("No L4 timesteps returned in the lookback window.")

    field4 = None
    for i in range(ds4.sizes["time"] - 1, -1, -1):
        cand = ds4[VAR].isel(time=i)
        if np.isfinite(cand.values).any():
            field4 = cand
            break
    if field4 is None:
        sys.exit("L4 returned only empty timesteps.")

    source_date = np.datetime_as_string(field4.time.values, unit="D")
    age_days = (now.date() - datetime.fromisoformat(source_date).date()).days

    lats, lons = ds4["latitude"].values, ds4["longitude"].values
    masks = {k: disc_mask(lats, lons, s["lat"], s["lon"], DISC_RADIUS_KM)
             for k, s in spots.items()}
    sites = site_values(spots, field4.values, l3_for(ds3, field4.time), masks)

    out = {
        "generated_utc": now.isoformat(timespec="seconds"),
        "source_date": source_date,
        "age_days": age_days,
        "product": PRODUCTS["nrt"][0],
        "variable": VAR,
        "disc_radius_km": DISC_RADIUS_KM,
        "sites": sites,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(OUT_PATH)      # atomic, so the app never reads a half file
    print(f"wrote {OUT_PATH} for {source_date} (age {age_days} d), {len(sites)} sites")

    n = append_rows(rows_from(source_date, sites, out["generated_utc"],
                              age_days, read_existing()))
    print(f"archive: {n} rows appended" if n
          else f"archive: {source_date} already recorded")


def run_backfill(spots, start_date, stream="my"):
    now = datetime.now(timezone.utc)
    start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    print(f"backfilling from the '{stream}' stream: {PRODUCTS[stream][0]}")
    ds4, ds3 = open_pair(spots, start, now, stream)

    n_times = ds4.sizes.get("time", 0)
    if n_times == 0:
        sys.exit("No L4 data in that range for this stream.")

    first = np.datetime_as_string(ds4["time"].values[0], unit="D")
    last = np.datetime_as_string(ds4["time"].values[-1], unit="D")
    print(f"L4 covers {first} to {last}, {n_times} days available")

    lats, lons = ds4["latitude"].values, ds4["longitude"].values
    masks = {k: disc_mask(lats, lons, s["lat"], s["lon"], DISC_RADIUS_KM)
             for k, s in spots.items()}

    seen = read_existing()
    fetched = now.isoformat(timespec="seconds")
    batch, skipped, empty = [], 0, 0

    for i in range(n_times):
        f4 = ds4[VAR].isel(time=i)
        source_date = np.datetime_as_string(f4.time.values, unit="D")
        if all((source_date, k) in seen for k in spots):
            skipped += 1
            continue
        v4 = f4.values
        if not np.isfinite(v4).any():
            empty += 1
            continue
        sites = site_values(spots, v4, l3_for(ds3, f4.time), masks)
        age = (now.date() - datetime.fromisoformat(source_date).date()).days
        batch += rows_from(source_date, sites, fetched, age, seen, stream)

        if i and i % 100 == 0:
            print(f"  ...{source_date} ({i}/{n_times})")

    n = append_rows(batch)
    print(f"backfill: {n} rows appended, {skipped} days already present, "
          f"{empty} empty")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", metavar="YYYY-MM-DD",
                    help="archive every available day from this date to now")
    ap.add_argument("--stream", choices=sorted(PRODUCTS), default="my",
                    help="which processing stream to backfill from "
                         "(default: my, the reprocessed archive)")
    args = ap.parse_args()

    spots = parse_spots()
    print(f"{len(spots)} spots: " + ", ".join(s["label"] for s in spots.values()))

    if args.backfill:
        run_backfill(spots, args.backfill, args.stream)
    else:
        run_nowcast(spots)


if __name__ == "__main__":
    main()
