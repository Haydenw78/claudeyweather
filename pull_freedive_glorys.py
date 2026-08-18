#!/usr/bin/env python3
"""Pull GLORYS daily currents for the Freedive Gold Coast site.

The raw Copernicus NetCDF is retained unchanged. A normalized CSV is derived
with explicit signed components and a direction-toward bearing. Authentication
is handled by ``copernicusmarine login`` or its standard environment variables;
credentials are never accepted by or written into this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

try:
    import copernicusmarine
except ImportError as exc:  # pragma: no cover - depends on the user's environment
    raise SystemExit(
        "copernicusmarine is not installed. Use Python 3.10-3.13 and run: "
        "python -m pip install copernicusmarine xarray netcdf4 pandas"
    ) from exc


PRODUCT_ID = "GLOBAL_MULTIYEAR_PHY_001_030"
DATASET_ID = "cmems_mod_glo_phy_my_0.083deg_P1D-m"
PRODUCT_URL = f"https://data.marine.copernicus.eu/product/{PRODUCT_ID}/description"
DEFAULT_LATITUDE = -27.942183
DEFAULT_LONGITUDE = 153.512083
DEFAULT_START = "2017-01-01T00:00:00"
DEFAULT_END = "2025-05-17T23:59:59"
TARGET_DEPTHS_M = (0.0, 40.0)
EXPECTED_VARIABLES = ("uo", "vo")
UNIT_FORMS = {
    "m/s", "m s-1", "m s^-1", "m s**-1", "m s⁻¹", "meter second-1",
    "metre second-1", "meters per second", "metres per second",
}


class PullError(RuntimeError):
    """Raised when downloaded data fail provenance or scientific checks."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/freedive-gc/glorys"))
    parser.add_argument("--latitude", type=float, default=DEFAULT_LATITUDE)
    parser.add_argument("--longitude", type=float, default=DEFAULT_LONGITUDE)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def response_path(response: Any, expected: Path) -> Path:
    candidate = getattr(response, "file_path", None)
    if candidate:
        if isinstance(candidate, (list, tuple)):
            if len(candidate) != 1:
                raise PullError(f"Expected one downloaded file, received {len(candidate)}")
            candidate = candidate[0]
        path = Path(candidate)
        if not path.is_absolute():
            path = expected.parent / path
        if path.exists():
            return path
    if expected.exists():
        return expected
    matches = sorted(expected.parent.glob(f"{expected.stem}*.nc"))
    if len(matches) == 1:
        return matches[0]
    raise PullError(f"Copernicus reported success but the NetCDF could not be resolved: {expected}")


def require_coordinate(ds: xr.Dataset, names: tuple[str, ...]) -> str:
    for name in names:
        if name in ds.coords or name in ds.dims:
            return name
    raise PullError(f"None of the expected coordinates {names} exist; found {list(ds.coords)}")


def check_component(variable: xr.DataArray, expected_standard_name: str) -> dict[str, Any]:
    attrs = dict(variable.attrs)
    units = str(attrs.get("units", "")).strip()
    standard_name = str(attrs.get("standard_name", "")).strip()
    if units.lower() not in UNIT_FORMS:
        raise PullError(f"{variable.name} units are {units!r}, not recognized metres per second")
    if standard_name and standard_name != expected_standard_name:
        raise PullError(
            f"{variable.name} standard_name is {standard_name!r}; expected {expected_standard_name!r}"
        )
    return {"units": units, "standard_name": standard_name or expected_standard_name}


def nearest_depths(available: np.ndarray) -> list[float]:
    finite = np.asarray(available, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise PullError("No finite depth coordinates were downloaded")
    selected: list[float] = []
    for target in TARGET_DEPTHS_M:
        actual = float(finite[np.argmin(np.abs(finite - target))])
        if actual not in selected:
            selected.append(actual)
    if len(selected) != len(TARGET_DEPTHS_M):
        raise PullError(f"Surface and 40 m targets resolved to the same model level: {selected}")
    return selected


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * radius_km * math.asin(math.sqrt(value))


def nearest_wet_point(
    ds: xr.Dataset,
    *,
    depth_name: str,
    depth_m: float,
    latitude_name: str,
    longitude_name: str,
    site_latitude: float,
    site_longitude: float,
) -> tuple[xr.Dataset, dict[str, float]]:
    """Select the closest grid cell with any valid u/v values at this depth."""
    layer = ds[["uo", "vo"]].sel({depth_name: depth_m})
    candidates: list[tuple[float, float, float, xr.Dataset]] = []
    for latitude in np.atleast_1d(layer[latitude_name].values).astype(float):
        for longitude in np.atleast_1d(layer[longitude_name].values).astype(float):
            point = layer.sel({latitude_name: latitude, longitude_name: longitude})
            u = np.asarray(point["uo"].values, dtype=float)
            v = np.asarray(point["vo"].values, dtype=float)
            if not np.any(np.isfinite(u) & np.isfinite(v)):
                continue
            distance = haversine_km(site_latitude, site_longitude, latitude, longitude)
            candidates.append((distance, latitude, longitude, point))
    if not candidates:
        raise PullError(
            f"No wet GLORYS cell with valid current exists near the site at model depth {depth_m} m"
        )
    distance, latitude, longitude, point = min(candidates, key=lambda item: item[0])
    return point, {
        "latitude": latitude,
        "longitude": longitude,
        "distance_from_site_km": distance,
        "model_depth_m": depth_m,
    }


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    raw_path = args.output / "freedive_gc_glorys_daily_currents.nc"
    csv_path = args.output / "freedive_gc_glorys_daily_currents.csv"
    manifest_path = args.output / "freedive_gc_glorys_manifest.json"

    if raw_path.exists() and not args.overwrite:
        print(f"Reusing existing raw file: {raw_path}")
    else:
        # A small box guarantees at least one wet model cell while keeping the
        # download tiny. The nearest cell to the operator-estimated coordinate
        # is selected explicitly during normalization.
        response = copernicusmarine.subset(
            dataset_id=DATASET_ID,
            variables=list(EXPECTED_VARIABLES),
            minimum_longitude=args.longitude - 0.06,
            maximum_longitude=args.longitude + 0.06,
            minimum_latitude=args.latitude - 0.06,
            maximum_latitude=args.latitude + 0.06,
            start_datetime=args.start,
            end_datetime=args.end,
            minimum_depth=0.0,
            maximum_depth=45.0,
            coordinates_selection_method="nearest",
            output_directory=str(args.output),
            output_filename=raw_path.name,
            overwrite=args.overwrite,
            file_format="netcdf",
            netcdf_compression_level=4,
            disable_progress_bar=False,
        )
        downloaded = response_path(response, raw_path)
        if downloaded.resolve() != raw_path.resolve():
            downloaded.replace(raw_path)

    with xr.open_dataset(raw_path) as ds:
        for variable in EXPECTED_VARIABLES:
            if variable not in ds:
                raise PullError(f"Required GLORYS variable {variable!r} is missing")

        latitude_name = require_coordinate(ds, ("latitude", "lat"))
        longitude_name = require_coordinate(ds, ("longitude", "lon"))
        depth_name = require_coordinate(ds, ("depth", "deptht", "lev"))
        time_name = require_coordinate(ds, ("time",))

        component_metadata = {
            "uo": check_component(ds["uo"], "eastward_sea_water_velocity"),
            "vo": check_component(ds["vo"], "northward_sea_water_velocity"),
        }

        selected_depths = nearest_depths(ds[depth_name].values)
        frames = []
        selected_cells: dict[str, dict[str, float]] = {}
        for label, depth_m in zip(("surface", "near_40m"), selected_depths):
            point, cell = nearest_wet_point(
                ds,
                depth_name=depth_name,
                depth_m=depth_m,
                latitude_name=latitude_name,
                longitude_name=longitude_name,
                site_latitude=args.latitude,
                site_longitude=args.longitude,
            )
            selected_cells[label] = cell
            part = point[["uo", "vo"]].to_dataframe().reset_index()
            part = part.rename(
                columns={
                    time_name: "time_utc",
                    "uo": "current_eastward_m_s",
                    "vo": "current_northward_m_s",
                }
            )
            part["target_depth_label"] = label
            part["model_depth_m"] = depth_m
            part["model_latitude"] = cell["latitude"]
            part["model_longitude"] = cell["longitude"]
            part["model_cell_distance_from_site_km"] = cell["distance_from_site_km"]
            frames.append(part)
        frame = pd.concat(frames, ignore_index=True)
        frame["time_utc"] = pd.to_datetime(frame["time_utc"])
        start_time = pd.Timestamp(args.start)
        end_time = pd.Timestamp(args.end)
        frame = frame[(frame["time_utc"] >= start_time) & (frame["time_utc"] <= end_time)].copy()
        frame["current_speed_m_s"] = np.hypot(
            frame["current_eastward_m_s"], frame["current_northward_m_s"]
        )
        frame["current_direction_toward_deg_true"] = (
            np.degrees(
                np.arctan2(frame["current_eastward_m_s"], frame["current_northward_m_s"])
            )
            + 360.0
        ) % 360.0
        frame["site_latitude"] = args.latitude
        frame["site_longitude"] = args.longitude
        frame["source_product_id"] = PRODUCT_ID
        frame["source_dataset_id"] = DATASET_ID

        ordered = [
            "time_utc", "target_depth_label", "model_depth_m",
            "current_eastward_m_s", "current_northward_m_s", "current_speed_m_s",
            "current_direction_toward_deg_true", "site_latitude", "site_longitude",
            "model_latitude", "model_longitude", "model_cell_distance_from_site_km",
            "source_product_id", "source_dataset_id",
        ]
        frame = frame[ordered].sort_values(["time_utc", "model_depth_m"])
        if frame[["current_eastward_m_s", "current_northward_m_s"]].dropna(how="all").empty:
            raise PullError("The selected model cell contains no current values")
        frame.to_csv(csv_path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")

        manifest = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "product_id": PRODUCT_ID,
            "dataset_id": DATASET_ID,
            "product_url": PRODUCT_URL,
            "source_type": "Copernicus GLORYS global ocean physics reanalysis, Level 4",
            "temporal_resolution": "daily",
            "requested_period": {"start": args.start, "end": args.end},
            "requested_site": {"latitude": args.latitude, "longitude": args.longitude},
            "selected_model_cells": selected_cells,
            "selected_model_depths_m": selected_depths,
            "component_metadata": component_metadata,
            "direction_derivation": (
                "bearing-toward clockwise from true north: "
                "degrees(atan2(eastward, northward)) modulo 360"
            ),
            "raw_sha256": sha256(raw_path),
            "raw_bytes": raw_path.stat().st_size,
            "normalized_rows": int(len(frame)),
            "valid_rows_by_depth": {
                label: int(part[["current_eastward_m_s", "current_northward_m_s"]].dropna().shape[0])
                for label, part in frame.groupby("target_depth_label")
            },
            "normalized_start": str(frame["time_utc"].min()),
            "normalized_end": str(frame["time_utc"].max()),
            "limitations": [
                "Reanalysis output is modelled and data-assimilated, not a current measurement at the reef.",
                "The selected grid cell is approximately 0.083 degrees and cannot resolve the Southport Seaway plume directly.",
                "Daily means cannot represent within-day tidal or current reversals.",
                "The historical dive reports do not consistently state observation time.",
            ],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Raw NetCDF: {raw_path}")
    print(f"Normalized CSV: {csv_path}")
    print(f"Manifest: {manifest_path}")
    print(f"Rows: {len(frame):,}; depths: {selected_depths}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
