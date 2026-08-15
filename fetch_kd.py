#!/usr/bin/env python3
"""
Kd490 nowcast fetcher.

Writes data/kd.json for the dive-vis app, in the same pattern as
data/observations.json.

Value comes from the Copernicus L4 gap-free field, which the pixel-level audit
showed preserves every paired L3 observation to floating-point precision.
Coverage accounting comes from L3, so every value ships with a record of how
much of its disc was actually observed rather than interpolated.

Auth: set COPERNICUSMARINE_SERVICE_USERNAME and
COPERNICUSMARINE_SERVICE_PASSWORD, or run `copernicusmarine login` once.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import copernicusmarine as cm

# --- config ---------------------------------------------------------------

L4_ID = "cmems_obs-oc_glo_bgc-transp_nrt_l4-gapfree-multi-4km_P1D"
L3_ID = "cmems_obs-oc_glo_bgc-transp_nrt_l3-multi-4km_P1D"
VAR = "KD490"

DISC_RADIUS_KM = 25.0
LOOKBACK_DAYS = 6          # L4 latency is ~2 days; walk back if the tail is short
OUT_PATH = Path("data/kd.json")

# Disc is 25 km so a few hundred metres of slop is harmless.
SITES = {
    "nine_mile":  {"lat": -28.19590, "lon": 153.62867, "label": "Nine Mile Reef"},
    "fidos":      {"lat": -28.19915, "lon": 153.59147, "label": "Fidos"},
    "palm_beach": {"lat": -28.10645, "lon": 153.47788, "label": "Palm Beach Reef"},
    "kirra":      {"lat": -28.16249, "lon": 153.53090, "label": "Kirra Reef"},
}

# Confidence ladder, keyed on interpolated fraction of the disc.
FLAG_THRESHOLDS = [
    (0.75, "weak_prior"),
    (0.50, "low_confidence"),
    (0.25, "flagged"),
]


def confidence(interp_frac):
    for cut, label in FLAG_THRESHOLDS:
        if interp_frac >= cut:
            return label
    return "ok"


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


def bbox(sites, pad_deg):
    lats = [s["lat"] for s in sites.values()]
    lons = [s["lon"] for s in sites.values()]
    return (
        min(lons) - pad_deg, max(lons) + pad_deg,
        min(lats) - pad_deg, max(lats) + pad_deg,
    )


# --- main -----------------------------------------------------------------

def main():
    missing = [k for k, v in SITES.items() if v["lat"] is None or v["lon"] is None]
    if missing:
        sys.exit(f"Site coordinates not set: {', '.join(missing)}")

    pad = DISC_RADIUS_KM / 111.0 + 0.1
    lon_min, lon_max, lat_min, lat_max = bbox(SITES, pad)

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=LOOKBACK_DAYS)

    common = dict(
        variables=[VAR],
        minimum_longitude=lon_min, maximum_longitude=lon_max,
        minimum_latitude=lat_min, maximum_latitude=lat_max,
        start_datetime=start, end_datetime=now,
        username=os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME"),
        password=os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"),
    )

    ds4 = cm.open_dataset(dataset_id=L4_ID, **common)
    ds3 = cm.open_dataset(dataset_id=L3_ID, **common)

    if ds4.sizes.get("time", 0) == 0:
        sys.exit("No L4 timesteps returned in the lookback window.")

    # Most recent L4 day that has any data over the region.
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

    # Matching L3 day. Absent is legitimate: treat the whole disc as interpolated.
    try:
        field3 = ds3[VAR].sel(time=field4.time, method="nearest", tolerance=np.timedelta64(1, "D"))
        l3_vals = field3.values
    except (KeyError, IndexError):
        l3_vals = None

    lats = ds4["latitude"].values
    lons = ds4["longitude"].values
    v4 = field4.values

    out = {
        "generated_utc": now.isoformat(timespec="seconds"),
        "source_date": source_date,
        "age_days": age_days,
        "product": L4_ID,
        "variable": VAR,
        "disc_radius_km": DISC_RADIUS_KM,
        "sites": {},
    }

    for key, site in SITES.items():
        m = disc_mask(lats, lons, site["lat"], site["lon"], DISC_RADIUS_KM)

        # L4 is gap-free over ocean and NaN over land, so finite L4 defines the
        # ocean cells of the disc.
        ocean = m & np.isfinite(v4)
        n_ocean = int(ocean.sum())
        if n_ocean == 0:
            out["sites"][key] = {"label": site["label"], "kd": None,
                                 "confidence": "no_data"}
            continue

        kd = float(np.mean(v4[ocean]))

        if l3_vals is None:
            n_obs = 0
        else:
            n_obs = int((ocean & np.isfinite(l3_vals)).sum())

        obs_frac = n_obs / n_ocean
        interp_frac = 1.0 - obs_frac

        out["sites"][key] = {
            "label": site["label"],
            "kd": round(kd, 5),
            "ocean_cells": n_ocean,
            "l3_observed_fraction": round(obs_frac, 3),
            "l4_interpolated_fraction": round(interp_frac, 3),
            "confidence": confidence(interp_frac),
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(OUT_PATH)          # atomic, so the app never reads a half file
    print(f"wrote {OUT_PATH} for {source_date} (age {age_days} d)")


if __name__ == "__main__":
    main()
