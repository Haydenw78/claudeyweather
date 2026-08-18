#!/usr/bin/env python3
"""Append-only capture of operational GLORYS current forecasts for Freedive GC.

Each run preserves one immutable forecast vintage. It downloads daily current
components from four days before capture through ten days after capture, then
derives the trailing five-day exposures used by the visibility investigation.

Authentication is handled by ``copernicusmarine login`` or Copernicus Marine's
standard environment variables. Credentials are never accepted or stored here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PRODUCT_ID = "GLOBAL_ANALYSISFORECAST_PHY_001_024"
DATASET_ID = "cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m"
PRODUCT_URL = f"https://data.marine.copernicus.eu/product/{PRODUCT_ID}/description"
DEFAULT_LATITUDE = -27.942183
DEFAULT_LONGITUDE = 153.512083
TARGET_DEPTHS_M = (0.0, 40.0)
HISTORY_DAYS = 4
FORECAST_DAYS = 10
EXPECTED_VARIABLES = ("uo", "vo")
UNIT_FORMS = {
    "m/s", "m s-1", "m s^-1", "m s**-1", "m s⁻¹", "meter second-1",
    "metre second-1", "meters per second", "metres per second",
}


class CaptureError(RuntimeError):
    """Raised when an operational capture cannot be trusted or preserved."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/freedive-gc/glorys-forecast"))
    parser.add_argument("--latitude", type=float, default=DEFAULT_LATITUDE)
    parser.add_argument("--longitude", type=float, default=DEFAULT_LONGITUDE)
    parser.add_argument(
        "--captured-at",
        type=parse_utc,
        help="UTC capture time for reproducible testing. Omit during normal operation.",
    )
    parser.add_argument(
        "--from-netcdf",
        type=Path,
        help="Normalize an existing operational NetCDF instead of downloading.",
    )
    return parser.parse_args()


def response_path(response: Any, expected: Path) -> Path:
    candidate = getattr(response, "file_path", None)
    if candidate:
        if isinstance(candidate, (list, tuple)):
            if len(candidate) != 1:
                raise CaptureError(f"Expected one downloaded file, received {len(candidate)}")
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
    raise CaptureError(f"Copernicus reported success but the NetCDF was not resolved: {expected}")


def require_coordinate(ds: xr.Dataset, names: tuple[str, ...]) -> str:
    for name in names:
        if name in ds.coords or name in ds.dims:
            return name
    raise CaptureError(f"None of the expected coordinates {names} exist; found {list(ds.coords)}")


def check_component(variable: xr.DataArray, expected_standard_name: str) -> dict[str, str]:
    units = str(variable.attrs.get("units", "")).strip()
    standard_name = str(variable.attrs.get("standard_name", "")).strip()
    if units.lower() not in UNIT_FORMS:
        raise CaptureError(f"{variable.name} units are {units!r}, not recognized metres per second")
    if standard_name and standard_name != expected_standard_name:
        raise CaptureError(
            f"{variable.name} standard_name is {standard_name!r}; expected {expected_standard_name!r}"
        )
    return {"units": units, "standard_name": standard_name or expected_standard_name}


def nearest_depths(available: np.ndarray) -> list[float]:
    finite = np.asarray(available, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise CaptureError("No finite depth coordinates were downloaded")
    selected = [float(finite[np.argmin(np.abs(finite - target))]) for target in TARGET_DEPTHS_M]
    if selected[0] == selected[1]:
        raise CaptureError(f"Surface and 40 m targets resolved to the same model level: {selected}")
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
        raise CaptureError(f"No wet GLORYS cell exists near the site at model depth {depth_m} m")
    distance, latitude, longitude, point = min(candidates, key=lambda item: item[0])
    return point, {
        "latitude": latitude,
        "longitude": longitude,
        "distance_from_site_km": distance,
        "model_depth_m": depth_m,
    }


def normalize_dataset(
    ds: xr.Dataset,
    *,
    site_latitude: float,
    site_longitude: float,
    captured_at_utc: datetime,
    requested_start: datetime,
    requested_end: datetime,
) -> tuple[pd.DataFrame, dict[str, dict[str, float]], dict[str, dict[str, str]]]:
    for variable in EXPECTED_VARIABLES:
        if variable not in ds:
            raise CaptureError(f"Required GLORYS variable {variable!r} is missing")

    latitude_name = require_coordinate(ds, ("latitude", "lat"))
    longitude_name = require_coordinate(ds, ("longitude", "lon"))
    depth_name = require_coordinate(ds, ("depth", "deptht", "lev"))
    time_name = require_coordinate(ds, ("time",))
    component_metadata = {
        "uo": check_component(ds["uo"], "eastward_sea_water_velocity"),
        "vo": check_component(ds["vo"], "northward_sea_water_velocity"),
    }
    selected_depths = nearest_depths(ds[depth_name].values)
    frames: list[pd.DataFrame] = []
    selected_cells: dict[str, dict[str, float]] = {}
    for label, depth_m in zip(("surface", "near_40m"), selected_depths):
        point, cell = nearest_wet_point(
            ds,
            depth_name=depth_name,
            depth_m=depth_m,
            latitude_name=latitude_name,
            longitude_name=longitude_name,
            site_latitude=site_latitude,
            site_longitude=site_longitude,
        )
        selected_cells[label] = cell
        part = point[["uo", "vo"]].to_dataframe().reset_index()
        part = part.rename(columns={
            time_name: "valid_time_utc",
            "uo": "current_eastward_m_s",
            "vo": "current_northward_m_s",
        })
        part["target_depth_label"] = label
        part["model_depth_m"] = depth_m
        part["model_latitude"] = cell["latitude"]
        part["model_longitude"] = cell["longitude"]
        part["model_cell_distance_from_site_km"] = cell["distance_from_site_km"]
        frames.append(part)

    frame = pd.concat(frames, ignore_index=True)
    frame["valid_time_utc"] = pd.to_datetime(frame["valid_time_utc"], utc=True)
    start = pd.Timestamp(requested_start)
    end = pd.Timestamp(requested_end)
    frame = frame[(frame["valid_time_utc"] >= start) & (frame["valid_time_utc"] <= end)].copy()
    frame["current_speed_m_s"] = np.hypot(
        frame["current_eastward_m_s"], frame["current_northward_m_s"]
    )
    frame["current_direction_toward_deg_true"] = (
        np.degrees(np.arctan2(frame["current_eastward_m_s"], frame["current_northward_m_s"]))
        + 360.0
    ) % 360.0
    capture_date = captured_at_utc.date()
    frame["forecast_vintage_utc"] = captured_at_utc
    frame["valid_date_utc"] = frame["valid_time_utc"].dt.date
    frame["lead_days_from_capture_date"] = frame["valid_date_utc"].map(
        lambda value: (value - capture_date).days
    )
    frame["time_role_at_capture"] = np.where(
        frame["lead_days_from_capture_date"] <= 0, "analysis_or_nowcast", "forecast"
    )
    frame["site_latitude"] = site_latitude
    frame["site_longitude"] = site_longitude
    frame["source_product_id"] = PRODUCT_ID
    frame["source_dataset_id"] = DATASET_ID

    ordered = [
        "forecast_vintage_utc", "valid_time_utc", "valid_date_utc",
        "lead_days_from_capture_date", "time_role_at_capture", "target_depth_label",
        "model_depth_m", "current_eastward_m_s", "current_northward_m_s",
        "current_speed_m_s", "current_direction_toward_deg_true", "site_latitude",
        "site_longitude", "model_latitude", "model_longitude",
        "model_cell_distance_from_site_km", "source_product_id", "source_dataset_id",
    ]
    frame = frame[ordered].sort_values(["valid_time_utc", "model_depth_m"]).reset_index(drop=True)
    counts = frame.groupby("target_depth_label")[["current_eastward_m_s", "current_northward_m_s"]].count()
    for label in ("surface", "near_40m"):
        if label not in counts.index or int(counts.loc[label].min()) < 5:
            raise CaptureError(f"Fewer than five complete current days were captured at {label}")
    return frame, selected_cells, component_metadata


def build_five_day_exposure(frame: pd.DataFrame, captured_at_utc: datetime) -> pd.DataFrame:
    capture_date = captured_at_utc.date()
    components = frame.pivot(
        index="valid_date_utc",
        columns="target_depth_label",
        values=["current_eastward_m_s", "current_northward_m_s", "current_speed_m_s"],
    ).sort_index()
    components.columns = [f"{label}_{field}" for field, label in components.columns]
    components.index = pd.to_datetime(components.index)
    exposure = pd.DataFrame(index=components.index)
    for label in ("surface", "near_40m"):
        for field in ("current_eastward_m_s", "current_northward_m_s", "current_speed_m_s"):
            source = f"{label}_{field}"
            exposure[f"{source}_5d_mean"] = components[source].rolling(5, min_periods=5).mean()
        u = exposure[f"{label}_current_eastward_m_s_5d_mean"]
        v = exposure[f"{label}_current_northward_m_s_5d_mean"]
        mean_speed = exposure[f"{label}_current_speed_m_s_5d_mean"]
        exposure[f"{label}_current_vector_speed_m_s_5d"] = np.hypot(u, v)
        exposure[f"{label}_current_direction_toward_deg_true_5d"] = (
            np.degrees(np.arctan2(u, v)) + 360.0
        ) % 360.0
        exposure[f"{label}_current_persistence_5d"] = np.hypot(u, v) / mean_speed

    exposure = exposure.reset_index(names="target_date_utc")
    exposure["target_date_utc"] = exposure["target_date_utc"].dt.date
    exposure["forecast_vintage_utc"] = captured_at_utc
    exposure["lead_days_from_capture_date"] = exposure["target_date_utc"].map(
        lambda value: (value - capture_date).days
    )
    exposure["exposure_start_date_utc"] = exposure["target_date_utc"].map(
        lambda value: value - timedelta(days=4)
    )
    exposure["analysis_or_nowcast_days_in_window"] = exposure["target_date_utc"].map(
        lambda target: sum(1 for offset in range(5) if target - timedelta(days=offset) <= capture_date)
    )
    exposure["forecast_days_in_window"] = 5 - exposure["analysis_or_nowcast_days_in_window"]
    exposure["fully_forecast_window"] = exposure["forecast_days_in_window"] == 5
    required = [
        f"{label}_current_{component}_m_s_5d_mean"
        for label in ("surface", "near_40m")
        for component in ("eastward", "northward")
    ]
    exposure = exposure[
        (exposure["lead_days_from_capture_date"] >= 0)
        & exposure[required].notna().all(axis=1)
    ].copy()
    ordered = [
        "forecast_vintage_utc", "target_date_utc", "lead_days_from_capture_date",
        "exposure_start_date_utc", "analysis_or_nowcast_days_in_window",
        "forecast_days_in_window", "fully_forecast_window",
    ] + [column for column in exposure.columns if column not in {
        "forecast_vintage_utc", "target_date_utc", "lead_days_from_capture_date",
        "exposure_start_date_utc", "analysis_or_nowcast_days_in_window",
        "forecast_days_in_window", "fully_forecast_window",
    }]
    return exposure[ordered].sort_values("target_date_utc").reset_index(drop=True)


def source_time_metadata(ds: xr.Dataset) -> dict[str, Any]:
    selected = {}
    for key, value in ds.attrs.items():
        lowered = key.lower()
        if any(token in lowered for token in ("date", "time", "forecast", "bulletin", "history")):
            selected[key] = str(value)
    return selected


def main() -> int:
    args = parse_args()
    captured_at = (args.captured_at or datetime.now(timezone.utc)).replace(microsecond=0)
    capture_date = captured_at.date()
    requested_start = datetime.combine(capture_date - timedelta(days=HISTORY_DAYS), time.min, timezone.utc)
    requested_end = datetime.combine(capture_date + timedelta(days=FORECAST_DAYS), time.max, timezone.utc)
    stamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    capture_dir = args.output / "captures" / stamp
    if capture_dir.exists():
        raise CaptureError(f"Capture vintage already exists and will not be overwritten: {capture_dir}")
    capture_dir.mkdir(parents=True)
    raw_path = capture_dir / "freedive_gc_glorys_operational_currents.nc"
    currents_path = capture_dir / "freedive_gc_glorys_operational_currents.csv"
    exposure_path = capture_dir / "freedive_gc_current_5d_exposure_forecast.csv"
    manifest_path = capture_dir / "manifest.json"

    try:
        import xarray as xr
    except ImportError as exc:
        raise SystemExit(
            "xarray is not installed. Activate the project environment and run: "
            "python -m pip install copernicusmarine xarray netcdf4 pandas"
        ) from exc

    if args.from_netcdf:
        source = args.from_netcdf.resolve()
        if not source.exists():
            raise CaptureError(f"Existing NetCDF does not exist: {source}")
        raw_path.write_bytes(source.read_bytes())
    else:
        try:
            import copernicusmarine
        except ImportError as exc:
            raise SystemExit(
                "copernicusmarine is not installed. Activate the project environment and run: "
                "python -m pip install copernicusmarine xarray netcdf4 pandas"
            ) from exc
        response = copernicusmarine.subset(
            dataset_id=DATASET_ID,
            variables=list(EXPECTED_VARIABLES),
            minimum_longitude=args.longitude - 0.10,
            maximum_longitude=args.longitude + 0.10,
            minimum_latitude=args.latitude - 0.10,
            maximum_latitude=args.latitude + 0.10,
            start_datetime=requested_start.isoformat(),
            end_datetime=requested_end.isoformat(),
            minimum_depth=0.0,
            maximum_depth=45.0,
            coordinates_selection_method="nearest",
            output_directory=str(capture_dir),
            output_filename=raw_path.name,
            overwrite=False,
            file_format="netcdf",
            netcdf_compression_level=4,
            disable_progress_bar=False,
        )
        downloaded = response_path(response, raw_path)
        if downloaded.resolve() != raw_path.resolve():
            downloaded.replace(raw_path)

    with xr.open_dataset(raw_path) as ds:
        metadata = source_time_metadata(ds)
        frame, selected_cells, component_metadata = normalize_dataset(
            ds,
            site_latitude=args.latitude,
            site_longitude=args.longitude,
            captured_at_utc=captured_at,
            requested_start=requested_start,
            requested_end=requested_end,
        )
    exposure = build_five_day_exposure(frame, captured_at)
    if exposure.empty:
        raise CaptureError("No complete five-day current exposures could be derived")

    frame.to_csv(currents_path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    exposure.to_csv(exposure_path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "captured_at_utc": captured_at.isoformat(),
        "capture_policy": "immutable forecast vintage; existing capture directories are never overwritten",
        "product_id": PRODUCT_ID,
        "dataset_id": DATASET_ID,
        "product_url": PRODUCT_URL,
        "requested_period": {"start": requested_start.isoformat(), "end": requested_end.isoformat()},
        "requested_site": {"latitude": args.latitude, "longitude": args.longitude},
        "selected_model_cells": selected_cells,
        "component_metadata": component_metadata,
        "direction_derivation": "bearing-toward clockwise from true north: degrees(atan2(eastward, northward)) modulo 360",
        "source_time_metadata": metadata,
        "raw_sha256": sha256(raw_path),
        "raw_bytes": raw_path.stat().st_size,
        "normalized_current_rows": int(len(frame)),
        "exposure_forecast_rows": int(len(exposure)),
        "valid_time_start": str(frame["valid_time_utc"].min()),
        "valid_time_end": str(frame["valid_time_utc"].max()),
        "lead_days_available": sorted(int(value) for value in exposure["lead_days_from_capture_date"].unique()),
        "exposure_definition": "trailing five calendar days ending on target date, including target date",
        "limitations": [
            "Operational model output forecasts the broad EAC and water-mass regime, not exact current over the reef.",
            "Daily means omit tidal and within-day current reversals.",
            "The surface and near-40 m layers may use different horizontal cells because of the model land/bed mask.",
            "Lead days are calculated from capture date; model bulletin metadata are retained separately when supplied.",
            "Forecast skill must be evaluated from these preserved vintages against later analyses or observations.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Capture: {capture_dir}")
    print(f"Raw NetCDF: {raw_path}")
    print(f"Normalized currents: {currents_path}")
    print(f"Five-day exposure forecasts: {exposure_path}")
    print(f"Manifest: {manifest_path}")
    print(f"Visibility forecast leads available: {manifest['lead_days_available']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
