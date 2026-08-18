#!/usr/bin/env python3
"""Reproducible downloader and normalizer for Queensland verified wave data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


API_ROOT = "https://www.data.qld.gov.au/api/3/action/package_show?id="
SEARCH_ROOT = ("https://www.data.qld.gov.au/api/3/action/package_search"
               "?q=coastal-data-system-waves&rows=200")
USER_AGENT = "vis-project-qld-wave-archive/1.0 (research data puller)"
AEST = timezone(timedelta(hours=10), name="AEST")

STATIONS = {
    "tweed-offshore": {
        "title": "Tweed Offshore",
        "slug": "coastal-data-system-waves-tweed-offshore",
        "buoy_depth_m": 60,
    },
    "tweed-heads": {
        "title": "Tweed Heads",
        "slug": "coastal-data-system-waves-tweed-heads",
        "buoy_depth_m": 22,
    },
    "bilinga": {
        "title": "Bilinga",
        "slug": "coastal-data-system-waves-bilinga",
        "buoy_depth_m": 18,
    },
    "palm-beach": {
        "title": "Palm Beach",
        "slug": "coastal-data-system-waves-palm-beach",
        "buoy_depth_m": 23,
    },
    "gold-coast": {
        "title": "Gold Coast",
        "slug": "coastal-data-system-waves-gold-coast",
        "buoy_depth_m": 17,
    },
    # Added after CKAN discovery. Depths are unknown and deliberately null
    # rather than guessed: buoy depth sets the dispersion solve, so a wrong
    # value is worse than an absent one. Confirm from deployment metadata.
    "mermaid-beach": {
        "title": "Mermaid Beach",
        "slug": "coastal-data-system-waves-mermaid-beach",
        "buoy_depth_m": None,
    },
    "brisbane": {
        # Off North Stradbroke, co-located with the IMOS reference station.
        # 25 resources, reported back to 1976.
        "title": "Brisbane",
        "slug": "coastal-data-system-waves-brisbane",
        "buoy_depth_m": None,
    },
    "brisbane-offshore": {
        "title": "Brisbane Offshore",
        "slug": "coastal-data-system-waves-brisbane-offshore",
        "buoy_depth_m": None,
    },
    "north-moreton": {
        "title": "North Moreton Bay",
        "slug": "coastal-data-system-waves-north-moreton",
        "buoy_depth_m": None,
    },
    "gladstone": {
        "title": "Gladstone",
        "slug": "coastal-data-system-waves-gladstone",
        "buoy_depth_m": None,
    },
    "bundaberg": {
        # Gladstone and Bundaberg bracket the 1770 departure point.
        "title": "Bundaberg",
        "slug": "coastal-data-system-waves-bundaberg",
        "buoy_depth_m": None,
    },
}

# Defects confirmed against the source data. Each nulls one field over one
# window and records why. Rows are kept: the other fields on them are sound,
# and dropping whole rows would silently thin the wave record too.
#
# Windows are half-open [start, end) in AEST.
EXCLUSIONS = [
    {
        "station": "tweed-heads",
        "fields": ["peak_direction_deg"],
        "start": "2023-08-01",
        "end": "2023-10-01",
        "reason": "compass_dead",
        "note": "2,903 rows read exactly 0.00 against a 0.02% background rate; "
                "no natural values at 0.01 while 89.99 occurs normally.",
    },
    {
        # Not corruption. The Mk3 and Mk4 resolve the high-frequency tail
        # differently, and Tz responds to that tail while Tp does not. The step
        # is -0.46 s, confirmed instrumental against the Tweed Heads control,
        # which was Mk4 throughout. Tp is also a different quantity before the
        # swap: continuous (9,022 distinct values) rather than binned (63).
        "station": "gold-coast",
        "fields": ["tz_s", "tp_s"],
        "start": "1987-01-01",
        "end": "2022-10-05",
        "reason": "pre_mk4_instrument_epoch",
        "note": "Excluded by default. Flip EXCLUDE_INSTRUMENT_EPOCHS to keep "
                "these and carry source_is_mk4 as a model term instead.",
    },
]

# The compass window is corruption and always goes. The Gold Coast epoch is a
# calibration difference across five years of otherwise good data, so it gets
# its own switch: excluding it costs far more than the compass window does.
EXCLUDE_INSTRUMENT_EPOCHS = True

OUTPUT_FIELDS = [
    "station",
    "buoy_depth_m",
    "timestamp_aest",
    "timestamp_utc",
    "hs_m",
    "hmax_m",
    "tz_s",
    "tp_s",
    "peak_direction_deg",
    "direction_datum",
    "sst_deg_c",
    "current_speed",
    "current_speed_unit",
    "current_direction_deg",
    "value_count",
    "parse_flags",
    "source_resource_id",
    "source_resource_name",
    "source_resource_year",
    "source_resource_modified",
    "source_is_mk4",
    "source_row_number",
]

WAVE_FIELDS = ["hs_m", "hmax_m", "tz_s", "tp_s", "peak_direction_deg", "sst_deg_c"]
CURRENT_FIELDS = ["current_speed", "current_direction_deg"]
VALUE_FIELDS = WAVE_FIELDS + CURRENT_FIELDS


class ArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class Resource:
    station: str
    buoy_depth_m: int | None
    dataset_id: str
    dataset_slug: str
    dataset_license: str
    resource_id: str
    resource_name: str
    resource_url: str
    resource_modified: str
    resource_position: int
    resource_year: int
    is_mk4: int
    datastore_active: bool
    datastore_complete: bool | None
    raw_path: Path


_THROTTLE = {"last": 0.0}
_THROTTLE_LOCK = __import__("threading").Lock()
MIN_REQUEST_GAP = 0.7          # seconds between source-file requests


def _pace() -> None:
    """Space requests out. The Queensland WAF is rate-based, and hammering it
    in parallel is what triggers the 403, not the request itself."""
    with _THROTTLE_LOCK:
        wait = MIN_REQUEST_GAP - (time.monotonic() - _THROTTLE["last"])
        if wait > 0:
            time.sleep(wait)
        _THROTTLE["last"] = time.monotonic()


def request_bytes(url: str, attempts: int = 9) -> bytes:
    delay = 2.0
    for attempt in range(1, attempts + 1):
        _pace()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in {403, 429, 500, 502, 503, 504} or attempt == attempts:
                raise
            retry_after = exc.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
            if exc.code == 403:
                # WAF challenge. Back off hard rather than hammering.
                wait = max(wait, 20.0 * attempt)
        except (TimeoutError, urllib.error.URLError):
            if attempt == attempts:
                raise
            wait = delay
        time.sleep(min(wait, 60.0))
        delay = min(delay * 2.0, 60.0)
    raise AssertionError("unreachable")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    atomic_write(path, encoded)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_dataset(slug: str) -> dict[str, Any]:
    payload = json.loads(request_bytes(API_ROOT + slug))
    if not payload.get("success"):
        raise ArchiveError(f"CKAN package_show failed for {slug}")
    return payload["result"]


def years_in_resource(name: str, url: str) -> list[int]:
    # Resource UUIDs can happen to contain four-digit sequences. Search the
    # human-readable name first, then only the download filename.
    years = [int(value) for value in re.findall(r"(?:19|20)\d{2}", name)]
    if not years:
        filename = url.rsplit("/", 1)[-1]
        years = [int(value) for value in re.findall(r"(?:19|20)\d{2}", filename)]
    return years


def resource_year(name: str, url: str) -> int:
    years = years_in_resource(name, url)
    if not years:
        raise ArchiveError(f"Could not determine year for resource {name!r}")
    return max(years)


def resource_is_selected(resource: dict[str, Any], requested_years: set[int]) -> bool:
    name = resource.get("name", "")
    if "wave data" not in name.lower() or resource.get("format", "").lower() != "csv":
        return False
    if not requested_years:
        return True
    years = years_in_resource(name, resource.get("url", ""))
    if not years:
        return False
    if len(years) >= 2:
        low, high = min(years), max(years)
        return any(low <= year <= high for year in requested_years)
    return years[0] in requested_years


def discover_resources(
    output: Path, station_names: list[str], requested_years: set[int]
) -> tuple[list[Resource], dict[str, Any]]:
    resources: list[Resource] = []
    datasets: dict[str, Any] = {}
    metadata_dir = output / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for station in station_names:
        config = STATIONS[station]
        dataset = fetch_dataset(config["slug"])
        datasets[station] = dataset
        save_json(metadata_dir / f"{station}.json", dataset)
        for position, item in enumerate(dataset.get("resources", [])):
            if not resource_is_selected(item, requested_years):
                continue
            rid = item["id"]
            name = item.get("name", rid)
            resources.append(
                Resource(
                    station=station,
                    buoy_depth_m=config["buoy_depth_m"],
                    dataset_id=dataset["id"],
                    dataset_slug=config["slug"],
                    dataset_license=dataset.get("license_id") or dataset.get("license_title") or "",
                    resource_id=rid,
                    resource_name=name,
                    resource_url=item["url"],
                    resource_modified=item.get("last_modified") or item.get("metadata_modified") or "",
                    resource_position=position,
                    resource_year=resource_year(name, item["url"]),
                    is_mk4=int("mk4" in (name + " " + item["url"]).lower()),
                    datastore_active=bool(item.get("datastore_active")),
                    datastore_complete=item.get("datastore_contains_all_records_of_source_file"),
                    raw_path=output / "raw" / station / f"{rid}.csv",
                )
            )
    return resources, datasets


def download_one(
    resource: Resource,
    prior: dict[str, Any],
    refresh: bool,
    allow_incomplete_datastore: bool,
) -> dict[str, Any]:
    old = prior.get(resource.resource_id, {})
    unchanged = (
        not refresh
        and resource.raw_path.exists()
        and old.get("resource_url") == resource.resource_url
        and old.get("resource_modified") == resource.resource_modified
        and old.get("sha256")
        and sha256_file(resource.raw_path) == old["sha256"]
    )
    if unchanged:
        checksum = old["sha256"]
        byte_count = resource.raw_path.stat().st_size
        status = "cached"
    else:
        data = request_bytes(resource.resource_url)
        download_source = "source_csv"
        if not data:
            if not resource.datastore_active:
                raise ArchiveError(
                    f"Source download was blocked and no DataStore exists for {resource.resource_name}"
                )
            if resource.datastore_complete is False and not allow_incomplete_datastore:
                raise ArchiveError(
                    f"Source download was blocked for {resource.station} {resource.resource_name}; "
                    "the available DataStore is explicitly incomplete. Retry from a network that can "
                    "download the source CSV. --allow-incomplete-datastore is available only for diagnostics."
                )
            dump_url = f"https://www.data.qld.gov.au/datastore/dump/{resource.resource_id}?bom=true"
            data = request_bytes(dump_url)
            download_source = "datastore_dump_incomplete" if resource.datastore_complete is False else "datastore_dump"
        if not data:
            raise ArchiveError(f"Downloaded zero bytes for {resource.resource_name}")
        atomic_write(resource.raw_path, data)
        checksum = hashlib.sha256(data).hexdigest()
        byte_count = len(data)
        status = "downloaded"
        time.sleep(0.15)
    return {
        "station": resource.station,
        "dataset_id": resource.dataset_id,
        "dataset_slug": resource.dataset_slug,
        "dataset_license": resource.dataset_license,
        "resource_id": resource.resource_id,
        "resource_name": resource.resource_name,
        "resource_url": resource.resource_url,
        "resource_modified": resource.resource_modified,
        "resource_position": resource.resource_position,
        "resource_year": resource.resource_year,
        "is_mk4": bool(resource.is_mk4),
        "raw_path": str(resource.raw_path.relative_to(resource.raw_path.parents[2])),
        "bytes": byte_count,
        "sha256": checksum,
        "download_status": status,
        "download_source": download_source if not unchanged else old.get("download_source", "cache"),
        "datastore_complete": resource.datastore_complete,
    }


def normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def map_columns(fieldnames: list[str]) -> dict[str, str]:
    normalized = {normalized_header(field): field for field in fieldnames if field}
    mapping: dict[str, str] = {}

    def find(output_name: str, exact: Iterable[str], contains: Iterable[str] = ()) -> None:
        for candidate in exact:
            if candidate in normalized:
                mapping[output_name] = normalized[candidate]
                return
        for key, original in normalized.items():
            if any(fragment in key for fragment in contains):
                mapping[output_name] = original
                return

    find("timestamp", ["datetimeaest", "datetime", "timestamp", "dateandtime"], ["datetime"])
    find("date", ["date", "recorddate"])
    find("time", ["time", "recordtime"])
    find("hs_m", ["hsm", "hs", "hsig", "significantwaveheight"], ["significantwaveheight"])
    find("hmax_m", ["hmaxm", "hmax", "maximumwaveheight"], ["hmax", "highestwave"])
    find("tz_s", ["tzs", "tz", "zeroupcrossingperiod"], ["zeroupcrossing"])
    find("tp_s", ["tps", "tp", "peakperiod"], ["peakperiod"])
    find(
        "peak_direction_deg",
        ["peakdirectiondegrees", "peakdirection", "wavedirection", "direction", "dir"],
        ["peakdirection", "wavedirection", "dirtp"],
    )
    find("sst_deg_c", ["sstdegreesc", "sst", "seatemp", "seasurfacetemperature"], ["sst", "seasurfacetemp"])
    find("current_speed", ["currentspeed", "currentspeedms", "currentspeedknots"], ["currentspeed"])
    find("current_direction_deg", ["currentdirection", "currentdirectiondegrees"], ["currentdirection"])
    if "timestamp" not in mapping and not ({"date", "time"} <= mapping.keys()):
        raise ArchiveError(f"No timestamp column in headers: {fieldnames}")
    if not any(field in mapping for field in WAVE_FIELDS + CURRENT_FIELDS):
        raise ArchiveError(f"No wave or current value column in headers: {fieldnames}")
    return mapping


def active_exclusions() -> list[dict]:
    return [e for e in EXCLUSIONS
            if EXCLUDE_INSTRUMENT_EPOCHS or e["reason"] != "pre_mk4_instrument_epoch"]


def exclusions_for(station: str, stamp: datetime) -> list[dict]:
    """Which exclusions bite on this station at this timestamp."""
    hits = []
    for e in active_exclusions():
        if e["station"] != station:
            continue
        start = datetime.fromisoformat(e["start"]).replace(tzinfo=AEST)
        end = datetime.fromisoformat(e["end"]).replace(tzinfo=AEST)
        if start <= stamp < end:
            hits.append(e)
    return hits


def canonical_tp(value: str) -> str:
    """Collapse the 2 dp / 3 dp formats onto one value per spectral bin.

    Tweed Heads changed format on 2 March 2022, so 22.22 and 22.222 are the
    same 0.045 Hz bin written two ways. Anything keyed on Tp doubles its bins
    unless they are collapsed.
    """
    if value == "":
        return value
    return format(round(float(value), 2), ".2f")


def direction_datum(source_header: str | None) -> str:
    """What the source claims about the direction reference, never what we assume.

    Queensland publishes at least two outputs for the same buoy-hour that both
    claim true north and differ by a constant offset equal to the local magnetic
    declination. Until that is resolved the datum is recorded, not asserted.
    """
    if not source_header:
        return "absent"
    header = source_header.lower()
    if "true" in header:
        return "true_claimed"
    if "magnetic" in header or "mag" in header.split():
        return "magnetic_claimed"
    return "unstated"


def current_speed_unit(source_header: str | None) -> str:
    if not source_header:
        return "absent"
    header = normalized_header(source_header)
    if "knot" in header or header.endswith("kn"):
        return "knots_claimed"
    if "ms" in header or "metrespersecond" in header:
        return "ms_claimed"
    return "unstated"


DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %I:%M %p",
    "%d-%m-%Y %H:%M",
    "%d %b %Y %H:%M",
    "%d %b %Y %I:%M %p",
    "%a, %d %b %Y %I:%M %p",
    "%Y%m%d%H%M",
)


SLASH_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\b")


def infer_date_order(values) -> str:
    """Decide day/month vs month/day for a whole resource, from evidence.

    Queensland's Gold Coast 2017 and 2018 archives use month/day while every
    other file uses day/month, and the column header is identical. Parsing each
    row independently rejects every day above 12 and silently misdates the rest,
    which retains exactly 12*12*48 = 6,912 rows of a 17,520-row year and shifts
    them onto the wrong date. So the order is inferred once per resource from
    any value where one component exceeds 12, and a resource that offers no
    such evidence is refused rather than guessed at.

    Returns 'dmy', 'mdy', or 'none' when no slash dates were seen at all.
    """
    saw_slash = False
    for value in values:
        match = SLASH_DATE.match(value or "")
        if not match:
            continue
        saw_slash = True
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 and second <= 12:
            return "dmy"
        if second > 12 and first <= 12:
            return "mdy"
    if not saw_slash:
        return "none"
    raise ValueError(
        "slash dates present but every value is ambiguous (no component above 12); "
        "refusing to guess day/month vs month/day"
    )


def parse_timestamp(value: str, order: str = "dmy") -> datetime:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValueError("empty timestamp")
    iso_value = cleaned[:-1] + "+00:00" if cleaned.endswith("Z") else cleaned
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        parsed = None
    if parsed is None and order == "mdy" and SLASH_DATE.match(cleaned):
        for fmt in MDY_FORMATS:
            try:
                parsed = datetime.strptime(cleaned, fmt)
                break
            except ValueError:
                pass
    if parsed is None:
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(cleaned, fmt)
                break
            except ValueError:
                pass
    if parsed is None:
        raise ValueError(f"unsupported timestamp {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=AEST)
    else:
        parsed = parsed.astimezone(AEST)
    return parsed


MDY_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
)

MISSING = {"", "na", "n/a", "nan", "null", "none", "-", "--", "missing"}
BOUNDS = {
    "hs_m": (0.0, 30.0),
    "hmax_m": (0.0, 60.0),
    "tz_s": (0.01, 40.0),
    "tp_s": (0.01, 40.0),
    "peak_direction_deg": (0.0, 360.0),
    "sst_deg_c": (-2.0, 40.0),
    "current_speed": (0.0, 20.0),
    "current_direction_deg": (0.0, 360.0),
}


def parse_number(field: str, value: str | None) -> tuple[str, str | None]:
    text = "" if value is None else value.strip()
    if text.lower() in MISSING:
        return "", None
    try:
        number = float(text)
    except ValueError:
        return "", f"{field}:non_numeric"
    if number <= -90.0:
        return "", f"{field}:sentinel"
    low, high = BOUNDS[field]
    if number < low or number > high:
        return "", f"{field}:out_of_range"
    if field in ("peak_direction_deg", "current_direction_deg") and number == 360.0:
        number = 0.0
    return format(number, ".12g"), None


def open_csv(path: Path):
    try:
        path.read_text(encoding="utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        encoding = "latin-1"
    return path.open("r", encoding=encoding, newline="")


def create_database(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE rows (
          station TEXT NOT NULL,
          buoy_depth_m INTEGER,
          timestamp_aest TEXT NOT NULL,
          timestamp_utc TEXT NOT NULL,
          hs_m TEXT, hmax_m TEXT, tz_s TEXT, tp_s TEXT,
          peak_direction_deg TEXT, direction_datum TEXT, sst_deg_c TEXT,
          current_speed TEXT, current_speed_unit TEXT, current_direction_deg TEXT,
          value_count INTEGER NOT NULL,
          parse_flags TEXT,
          source_resource_id TEXT NOT NULL,
          source_resource_name TEXT NOT NULL,
          source_resource_year INTEGER NOT NULL,
          source_resource_modified TEXT NOT NULL,
          source_resource_position INTEGER NOT NULL,
          source_is_mk4 INTEGER NOT NULL,
          source_row_number INTEGER NOT NULL
        )
        """
    )
    return connection


def parse_resource(connection: sqlite3.Connection, resource: Resource) -> dict[str, Any]:
    parsed_rows = 0
    rejected_rows = 0
    flagged_rows = 0
    first_timestamp = None
    last_timestamp = None
    timestamps: list[datetime] = []
    insert_sql = "INSERT INTO rows VALUES (" + ",".join("?" * 23) + ")"
    empty_rows = 0
    excluded_values = 0
    batch: list[tuple[Any, ...]] = []
    with open_csv(resource.raw_path) as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ArchiveError(f"No header in {resource.raw_path}")
        mapping = map_columns(reader.fieldnames)
        rows_cache = list(reader)
        ts_col = mapping.get("timestamp")
        raw_stamps = [(r.get(ts_col) or "") for r in rows_cache] if ts_col else []
        date_order = infer_date_order(raw_stamps)
        datum = direction_datum(mapping.get("peak_direction_deg"))
        speed_unit = current_speed_unit(mapping.get("current_speed"))
        for row_number, source in enumerate(rows_cache, start=2):
            raw_timestamp = (
                source.get(mapping["timestamp"], "")
                if "timestamp" in mapping
                else f"{source.get(mapping['date'], '')} {source.get(mapping['time'], '')}"
            )
            try:
                stamp = parse_timestamp(raw_timestamp, date_order)
            except ValueError:
                rejected_rows += 1
                continue
            values: dict[str, str] = {}
            flags: list[str] = []
            for field in VALUE_FIELDS:
                value, flag = parse_number(field, source.get(mapping[field])) if field in mapping else ("", f"{field}:column_missing")
                values[field] = value
                if flag:
                    flags.append(flag)
            if values["tp_s"] != "":
                values["tp_s"] = canonical_tp(values["tp_s"])
            for rule in exclusions_for(resource.station, stamp):
                for field in rule["fields"]:
                    if values.get(field, "") != "":
                        values[field] = ""
                        flags.append(f"{field}:excluded_{rule['reason']}")
                        excluded_values += 1
            value_count = sum(1 for field in VALUE_FIELDS if values[field] != "")
            if value_count == 0:
                empty_rows += 1
            parsed_rows += 1
            flagged_rows += int(bool(flags))
            timestamps.append(stamp)
            first_timestamp = stamp if first_timestamp is None or stamp < first_timestamp else first_timestamp
            last_timestamp = stamp if last_timestamp is None or stamp > last_timestamp else last_timestamp
            batch.append(
                (
                    resource.station,
                    resource.buoy_depth_m,
                    stamp.isoformat(timespec="minutes"),
                    stamp.astimezone(timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z"),
                    values["hs_m"], values["hmax_m"], values["tz_s"], values["tp_s"],
                    values["peak_direction_deg"], datum, values["sst_deg_c"],
                    values["current_speed"], speed_unit, values["current_direction_deg"],
                    value_count,
                    ";".join(flags), resource.resource_id, resource.resource_name,
                    resource.resource_year, resource.resource_modified, resource.resource_position,
                    resource.is_mk4, row_number,
                )
            )
            if len(batch) >= 5000:
                connection.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            connection.executemany(insert_sql, batch)
    connection.commit()
    deltas = [int((b - a).total_seconds() // 60) for a, b in zip(sorted(set(timestamps)), sorted(set(timestamps))[1:])]
    cadence = mode_positive(deltas)
    return {
        "station": resource.station,
        "resource_id": resource.resource_id,
        "resource_name": resource.resource_name,
        "source_rows": parsed_rows + rejected_rows,
        "parsed_rows": parsed_rows,
        "rejected_timestamp_rows": rejected_rows,
        "flagged_rows": flagged_rows,
        "empty_value_rows": empty_rows,
        "excluded_values": excluded_values,
        "date_order": date_order,
        "direction_datum": datum,
        "current_speed_unit": speed_unit,
        "source_columns": " | ".join(f"{k}={v}" for k, v in sorted(mapping.items())),
        "first_timestamp_aest": first_timestamp.isoformat(timespec="minutes") if first_timestamp else "",
        "last_timestamp_aest": last_timestamp.isoformat(timespec="minutes") if last_timestamp else "",
        "modal_cadence_minutes": cadence or "",
    }


def mode_positive(values: list[int]) -> int | None:
    counts: dict[int, int] = {}
    for value in values:
        if value > 0:
            counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda item: (counts[item], -item)) if counts else None


def grid_step(values: list[int]) -> int | None:
    plausible = [value for value in values if 0 < value <= 180]
    if not plausible:
        return None
    step = plausible[0]
    for value in plausible[1:]:
        step = math.gcd(step, value)
    return step


def selected_rows_sql(station: str | None = None) -> tuple[str, tuple[Any, ...]]:
    where = "WHERE station = ?" if station else ""
    params: tuple[Any, ...] = (station,) if station else ()
    sql = f"""
      SELECT * FROM (
        SELECT rows.*,
          ROW_NUMBER() OVER (
            PARTITION BY station, timestamp_aest
            ORDER BY (value_count > 0) DESC,
                     source_resource_modified DESC, source_resource_year DESC,
                     source_is_mk4 DESC, source_resource_position DESC,
                     source_resource_id DESC, source_row_number DESC
          ) AS preference_rank,
          COUNT(*) OVER (PARTITION BY station, timestamp_aest) AS candidate_count
        FROM rows {where}
      ) WHERE preference_rank = 1
    """
    return sql, params


def output_row(record: sqlite3.Row) -> dict[str, Any]:
    return {field: record[field] for field in OUTPUT_FIELDS}


def write_normalized(connection: sqlite3.Connection, output: Path, station_names: list[str]) -> None:
    connection.row_factory = sqlite3.Row
    normalized = output / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    base_columns = ",".join(OUTPUT_FIELDS)
    for station in station_names:
        all_path = normalized / f"{station}.csv"
        with all_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            query = f"SELECT {base_columns} FROM rows WHERE station=? ORDER BY timestamp_aest, source_resource_id, source_row_number"
            for record in connection.execute(query, (station,)):
                writer.writerow(output_row(record))
        selected_path = normalized / f"{station}-deduplicated.csv"
        sql, params = selected_rows_sql(station)
        with selected_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for record in connection.execute(sql + " ORDER BY timestamp_aest", params):
                writer.writerow(output_row(record))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_qc(connection: sqlite3.Connection, output: Path, resource_qc: list[dict[str, Any]]) -> None:
    qc_dir = output / "qc"
    write_csv(qc_dir / "resources.csv", resource_qc, list(resource_qc[0]) if resource_qc else ["station"])
    connection.row_factory = sqlite3.Row
    sql, params = selected_rows_sql()
    selected = list(connection.execute(sql, params))
    by_station_year: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for row in selected:
        year = int(row["timestamp_aest"][:4])
        by_station_year.setdefault((row["station"], year), []).append(row)
    station_rows: list[dict[str, Any]] = []
    for (station, year), records in sorted(by_station_year.items()):
        records.sort(key=lambda row: row["timestamp_aest"])
        stamps = [parse_timestamp(row["timestamp_aest"]) for row in records]
        deltas = [int((b - a).total_seconds() // 60) for a, b in zip(stamps, stamps[1:])]
        cadence = mode_positive(deltas)
        step = grid_step(deltas)
        expected = (
            int((stamps[-1] - stamps[0]).total_seconds() // 60 // step) + 1
            if step and stamps else len(stamps)
        )
        valid = {field: sum(bool(row[field]) for row in records) for field in VALUE_FIELDS}

        # Defects that no single row can reveal. A dead compass reports 0.00 and
        # passes every per-row bound; a decimal-format change splits one spectral
        # frequency bin into two distinct values. Both are only visible in aggregate.
        directions = [row["peak_direction_deg"] for row in records if row["peak_direction_deg"]]
        zero_directions = sum(1 for value in directions if float(value) == 0.0)
        tp_values = {row["tp_s"] for row in records if row["tp_s"]}
        tp_rounded = {format(float(value), ".2f") for value in tp_values}
        instruments = {int(row["source_is_mk4"]) for row in records}
        year_start = datetime(year, 1, 1, tzinfo=AEST)
        year_end = datetime(year + 1, 1, 1, tzinfo=AEST)
        calendar_slots = (
            int((min(year_end, datetime.now(AEST)) - year_start).total_seconds() // 60 // step)
            if step else 0
        )
        station_rows.append(
            {
                "station": station,
                "year": year,
                "first_timestamp_aest": stamps[0].isoformat(timespec="minutes"),
                "last_timestamp_aest": stamps[-1].isoformat(timespec="minutes"),
                "deduplicated_rows": len(records),
                "modal_cadence_minutes": cadence or "",
                "inferred_grid_step_minutes": step or "",
                "span_coverage_fraction": format(len(records) / expected, ".6f") if expected else "",
                "calendar_coverage_fraction": format(len(records) / calendar_slots, ".6f") if calendar_slots else "",
                "instrument_mix": "mk4" if instruments == {1} else "pre_mk4" if instruments == {0} else "mixed",
                "direction_exact_zero_count": zero_directions,
                "direction_exact_zero_fraction": format(zero_directions / len(directions), ".6f") if directions else "",
                "direction_datums": ";".join(sorted({row["direction_datum"] or "" for row in records})),
                "tp_distinct_values": len(tp_values),
                "tp_distinct_at_2dp": len(tp_rounded),
                "tp_decimal_split_suspected": int(len(tp_values) > len(tp_rounded)),
                **{f"valid_{field}_fraction": format(valid[field] / len(records), ".6f") for field in VALUE_FIELDS},
            }
        )
    station_fields = list(station_rows[0]) if station_rows else ["station", "year"]
    write_csv(qc_dir / "stations.csv", station_rows, station_fields)
    duplicate_rows: list[dict[str, Any]] = []
    duplicate_query = """
      SELECT rows.*,
        ROW_NUMBER() OVER (
          PARTITION BY station, timestamp_aest
          ORDER BY (value_count > 0) DESC,
                   source_resource_modified DESC, source_resource_year DESC,
                   source_is_mk4 DESC, source_resource_position DESC,
                   source_resource_id DESC, source_row_number DESC
        ) AS preference_rank,
        COUNT(*) OVER (PARTITION BY station, timestamp_aest) AS candidate_count
      FROM rows
    """
    duplicate_fields = ["station", "timestamp_aest", "candidate_count", "selected", "source_resource_id", "source_resource_name", "source_row_number"]
    for row in connection.execute(f"SELECT * FROM ({duplicate_query}) WHERE candidate_count > 1 ORDER BY station,timestamp_aest,preference_rank"):
        duplicate_rows.append(
            {
                "station": row["station"],
                "timestamp_aest": row["timestamp_aest"],
                "candidate_count": row["candidate_count"],
                "selected": int(row["preference_rank"] == 1),
                "source_resource_id": row["source_resource_id"],
                "source_resource_name": row["source_resource_name"],
                "source_row_number": row["source_row_number"],
            }
        )
    write_csv(qc_dir / "duplicates.csv", duplicate_rows, duplicate_fields)


def command_stations(args: argparse.Namespace) -> int:
    """List every wave station CKAN actually publishes, with its real slug.

    Slugs are discovered rather than guessed. Three dead ends in this project
    came from assumed dataset names.
    """
    payload = json.loads(request_bytes(SEARCH_ROOT).decode("utf-8"))
    results = payload.get("result", {}).get("results", [])
    known = {c["slug"]: name for name, c in STATIONS.items()}
    rows = []
    for package in results:
        slug = package.get("name", "")
        if "coastal-data-system-waves" not in slug:
            continue
        rows.append((
            "configured" if slug in known else "NEW",
            known.get(slug, ""),
            slug,
            package.get("title", ""),
            len(package.get("resources", [])),
            package.get("license_id", ""),
        ))
    rows.sort(key=lambda r: (r[0] != "NEW", r[2]))
    print(f"{'state':<11}{'key':<20}{'slug':<52}{'res':>5}  licence")
    for state, key, slug, title, n, lic in rows:
        print(f"{state:<11}{key:<20}{slug:<52}{n:>5}  {lic}")
    print(f"\n{len(rows)} wave datasets published, {len(STATIONS)} configured.")
    return 0


def command_sync(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stations = args.station or list(STATIONS)
    invalid = sorted(set(stations) - set(STATIONS))
    if invalid:
        raise ArchiveError(f"Unknown station(s): {', '.join(invalid)}")
    resources, datasets = discover_resources(output, stations, set(args.year or []))
    prior_manifest = load_json(output / "manifest.json", {})
    prior = {item["resource_id"]: item for item in prior_manifest.get("resources", [])}
    print(f"Discovered {len(resources)} CSV resources across {len(stations)} stations.")
    entries: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_one, item, prior, args.refresh, args.allow_incomplete_datastore): item
            for item in resources
        }
        for future in as_completed(futures):
            item = futures[future]
            entry = future.result()
            entries.append(entry)
            print(f"{entry['download_status']:>10}  {item.station:15} {item.resource_name}")
    entries.sort(key=lambda item: (item["station"], item["resource_year"], item["resource_id"]))
    manifest = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_root": API_ROOT,
        "stations": {
            station: {
                "title": STATIONS[station]["title"],
                "buoy_depth_m": STATIONS[station]["buoy_depth_m"],
                "dataset_slug": STATIONS[station]["slug"],
                "dataset_id": datasets[station]["id"],
                "license": datasets[station].get("license_id") or datasets[station].get("license_title") or "",
            }
            for station in stations
        },
        "resources": entries,
    }
    save_json(output / "manifest.json", manifest)
    database_path = output / "normalized" / ".build.sqlite"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = create_database(database_path)
    resource_qc: list[dict[str, Any]] = []
    try:
        for resource in sorted(resources, key=lambda item: (item.station, item.resource_year, item.resource_id)):
            print(f"   parsing  {resource.station:15} {resource.resource_name}")
            resource_qc.append(parse_resource(connection, resource))
        connection.execute("CREATE INDEX row_station_time ON rows(station,timestamp_aest)")
        connection.commit()
        write_normalized(connection, output, stations)
        write_qc(connection, output, resource_qc)
    finally:
        connection.close()
        if database_path.exists() and not args.keep_database:
            database_path.unlink()
        wal = database_path.with_name(database_path.name + "-wal")
        shm = database_path.with_name(database_path.name + "-shm")
        for sidecar in (wal, shm):
            if sidecar.exists():
                sidecar.unlink()
    print(f"Complete: {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    stations = subparsers.add_parser(
        "stations", help="list every wave station CKAN publishes, with real slugs")
    stations.set_defaults(func=command_stations)

    sync = subparsers.add_parser("sync", help="discover, download, normalize and run QC")
    sync.add_argument("--output", type=Path, required=True)
    sync.add_argument("--station", action="append", choices=sorted(STATIONS), help="repeat to select stations")
    sync.add_argument("--year", action="append", type=int, help="repeat to select resource years")
    sync.add_argument("--workers", type=int, default=1, choices=range(1, 5))
    sync.add_argument("--refresh", action="store_true", help="redownload even when metadata and checksum match")
    sync.add_argument(
        "--allow-incomplete-datastore",
        action="store_true",
        help="diagnostic fallback only: accept a DataStore explicitly marked incomplete if source CSV access is blocked",
    )
    sync.add_argument("--keep-database", action="store_true", help="keep the intermediate SQLite database")
    sync.set_defaults(func=command_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ArchiveError, OSError, urllib.error.URLError, json.JSONDecodeError, csv.Error) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
