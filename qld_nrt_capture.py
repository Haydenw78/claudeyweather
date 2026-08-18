#!/usr/bin/env python3
"""Permanent capture of the Queensland near-real-time wave/current feed.

The source is a single CKAN DataStore resource covering every Queensland wave
buoy at once, updated hourly, retaining roughly seven days:

  resource id  2bbef99e-9974-49b9-a316-57402b00609c
  dump url     https://www.data.qld.gov.au/datastore/dump/<id>?bom=true
  resource page https://www.data.qld.gov.au/dataset/coastal-data-system-near-real-time-wave-data/resource/2bbef99e-9974-49b9-a316-57402b00609c
  licence      CC BY 4.0 as shown on the resource page (see the licensing
               note in the raw/, normalized/ and qc/ output before assuming
               that covers every station equally - Tweed Offshore's separate
               historical-current resource is CC BY-ND 4.0, and this feed has
               never stated per-station licence terms of its own)

This is deliberately a SEPARATE module from qld_wave_archive.py, not an
extension of it. qld_wave_archive.py's model is "the source has everything,
redownload and rebuild the normalized view from scratch every run" - correct
for a stable multi-year archive with one resource per station-year. This
feed's model has to be the opposite: the source keeps ~7 days and then the
data is gone, so every capture must be preserved permanently and nothing may
ever be silently overwritten, no matter how the model has to be rebuilt from
that history each time. Reusing that model would either lose data (rebuild
from scratch each run) or need a full duplicate of the loop it already got
right. So the pieces that ARE source-agnostic - HTTP fetch with backoff,
atomic file writes, JSON load/save, sha256, the AEST constant, and the
"claimed, not asserted" pattern for current-speed units and direction datum -
are imported from qld_wave_archive.py, not reimplemented.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import qld_wave_archive as archive

RESOURCE_ID = "2bbef99e-9974-49b9-a316-57402b00609c"
SOURCE_URL = f"https://www.data.qld.gov.au/datastore/dump/{RESOURCE_ID}?bom=true"
RESOURCE_PAGE = (
    "https://www.data.qld.gov.au/dataset/coastal-data-system-near-real-time-wave-data"
    f"/resource/{RESOURCE_ID}"
)
LICENCE_AS_SHOWN = "CC BY 4.0"  # as shown on RESOURCE_PAGE at capture time - not interpreted, just recorded

# Source columns this feed has always published, verbatim (order as CKAN emits
# them). "_id" is CKAN's own DataStore row id, not part of the source
# observation, and is dropped rather than carried into the identity or the raw
# capture: it is not stable across dumps of the same underlying row.
REQUIRED_COLUMNS = [
    "Site", "SiteNumber", "Seconds", "DateTime", "Latitude", "Longitude",
    "Hsig", "Hmax", "Tp", "Tz", "SST", "Direction", "Current Speed", "Current Direction",
]
KNOWN_EXTRA_COLUMNS = {"_id"}

# QC-only. Every station in the dump is captured regardless of this list; see
# requirement that this must never gate capture, only reporting.
LOCAL_STATIONS = ["Bilinga", "Gold Coast Mk4", "Palm Beach Mk4", "Tweed Heads Mk4", "Tweed Offshore"]

# Same convention as qld_wave_archive.py's parse_number(): a source value at or
# below -90 is a sentinel, not a measurement. Kept as one shared threshold
# rather than a second magic number, since it is the same feed family and the
# same instruments report the same way.
SENTINEL_THRESHOLD = -90.0
VALUE_FIELDS = ["Hsig", "Hmax", "Tp", "Tz", "SST", "Direction", "Current Speed", "Current Direction"]

RAW_FIELDS = REQUIRED_COLUMNS + ["_captured_at_utc", "_revision"]
NORMALIZED_FIELDS = [
    "site", "site_number", "timestamp_utc", "timestamp_aest", "latitude", "longitude",
    "hs_m", "hmax_m", "tp_s", "tz_s", "sst_deg_c", "peak_direction_deg",
    "current_speed", "current_speed_unit", "current_direction_deg", "direction_datum",
    "revision", "captured_at_utc",
]


class CaptureError(RuntimeError):
    """Raised for every fail-loud condition. Never caught silently - the
    caller (CLI or workflow) is meant to exit non-zero and print this."""


@dataclass
class Row:
    """One source row, values preserved exactly as strings. Nothing here
    converts, rounds or defaults anything - that only happens in normalize()."""
    values: dict[str, str]

    @property
    def site(self) -> str:
        return self.values["Site"]

    @property
    def site_number(self) -> str:
        return self.values["SiteNumber"]

    @property
    def seconds(self) -> str:
        return self.values["Seconds"]

    @property
    def identity(self) -> tuple[str, str]:
        return (self.site_number, self.seconds)

    @property
    def month_key(self) -> str:
        # Partition by the AEST calendar month in DateTime, not Seconds/UTC -
        # a human looking for "August's data" means AEST August.
        return self.values["DateTime"][:7]

    def content_hash(self) -> str:
        # Hash every source column, not just the value fields. If Site,
        # Latitude or Longitude drift for the same identity that is still a
        # revision worth keeping, not something to ignore because only the
        # measurement fields were being watched.
        payload = "|".join(self.values.get(col, "") for col in REQUIRED_COLUMNS)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---- fetch ------------------------------------------------------------------

def fetch_dump() -> bytes:
    """No API key: this is a public DataStore dump, fetched exactly as a
    browser would fetch it."""
    return archive.request_bytes(SOURCE_URL)


# ---- parse & schema verification --------------------------------------------

def parse_dump(data: bytes) -> tuple[list[Row], list[str]]:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []
    rows = [Row(values={k: (v if v is not None else "") for k, v in record.items() if k is not None})
            for record in reader]
    return rows, fieldnames


def verify_schema(fieldnames: list[str]) -> dict[str, list[str]]:
    """Returns {"missing": [...], "unexpected": [...]}. Does not raise - the
    caller decides whether missing/unexpected columns are fatal, so the QC
    snapshot can be written even when the run is about to fail loudly."""
    present = set(fieldnames)
    expected = set(REQUIRED_COLUMNS) | KNOWN_EXTRA_COLUMNS
    missing = [c for c in REQUIRED_COLUMNS if c not in present]
    unexpected = [c for c in fieldnames if c not in expected]
    return {"missing": missing, "unexpected": unexpected}


def verify_utc_aest(rows: list[Row]) -> list[tuple[str, str, str, str]]:
    """Returns a list of (site, site_number, seconds, datetime) for every row
    where Seconds (Unix UTC) does not convert to the AEST DateTime Queensland
    published alongside it. Queensland does not observe daylight saving, so
    the offset is always a fixed +10; if this ever disagrees for a non-corrupt
    row, something upstream has changed and must not be silently trusted.

    DateTime is parsed with archive.parse_timestamp() (reused, not
    reimplemented) rather than compared as a string, because at least one
    station (Abbot Point TriAxys) publishes "YYYY-MM-DD HH:MM:SS" - a space,
    not the "T" every other station uses. That is a formatting difference,
    not a timestamp error, and a string comparison would misreport it as one.
    Comparison is still exact to the second: a station that is genuinely
    minutes off (seen once at One Tree Island - Seconds converts to
    01:30:00, DateTime says 01:31:00) is a real inconsistency worth failing
    on, not something to round away.
    """
    mismatches = []
    for row in rows:
        try:
            secs = int(row.seconds)
        except ValueError:
            mismatches.append((row.site, row.site_number, row.seconds, row.values.get("DateTime", "")))
            continue
        expected_aest = datetime.fromtimestamp(secs, tz=timezone.utc).astimezone(archive.AEST)
        raw_datetime = row.values.get("DateTime", "")
        try:
            published_aest = archive.parse_timestamp(raw_datetime)
        except ValueError:
            mismatches.append((row.site, row.site_number, row.seconds, raw_datetime))
            continue
        if published_aest != expected_aest:
            mismatches.append((row.site, row.site_number, row.seconds, raw_datetime))
    return mismatches


# ---- sentinel / validity ------------------------------------------------------

def is_sentinel_or_missing(value: str) -> bool:
    text = (value or "").strip()
    if text == "":
        return True
    try:
        number = float(text)
    except ValueError:
        return True
    return number <= SENTINEL_THRESHOLD


def valid(row: Row, field: str) -> bool:
    return not is_sentinel_or_missing(row.values.get(field, ""))


# ---- raw capture: append-only, revision-tracked -----------------------------

def load_existing_month(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Latest known row per identity in an existing month file. Empty dict if
    the file does not exist yet - that is the normal case for a new month,
    not an error."""
    latest: dict[tuple[str, str], dict[str, str]] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for record in reader:
            identity = (record["SiteNumber"], record["Seconds"])
            existing = latest.get(identity)
            if existing is None or int(record["_revision"]) > int(existing["_revision"]):
                latest[identity] = record
    return latest


def partition_new_rows(
    rows: list[Row], captured_at_utc: str, output: Path
) -> tuple[dict[str, list[dict[str, str]]], int, int]:
    """Groups incoming rows by month, decides which are genuinely new content
    for their identity (append, revision 1), which are a changed revision of
    a previously captured identity (append, revision N+1), and which are an
    exact repeat of what is already the latest revision on disk (skip, never
    appended). Returns (by_month_new_rows, duplicate_count, revised_count).

    This function does not write anything - callers decide when and where.
    Keeping the decision pure makes it testable without touching the
    filesystem, which is most of what the fixture tests below exercise.
    """
    by_month: dict[str, list[Row]] = {}
    for row in rows:
        by_month.setdefault(row.month_key, []).append(row)

    result: dict[str, list[dict[str, str]]] = {}
    duplicate_count = 0
    revised_count = 0
    for month, month_rows in by_month.items():
        existing_path = raw_month_path(output, month)
        existing = load_existing_month(existing_path)
        new_for_month: list[dict[str, str]] = []
        for row in month_rows:
            content = row.content_hash()
            prior = existing.get(row.identity)
            if prior is not None:
                prior_content = Row(values={k: prior.get(k, "") for k in REQUIRED_COLUMNS}).content_hash()
                if prior_content == content:
                    duplicate_count += 1
                    continue
                revision = int(prior["_revision"]) + 1
                revised_count += 1
            else:
                revision = 1
            out_row = dict(row.values)
            out_row["_captured_at_utc"] = captured_at_utc
            out_row["_revision"] = str(revision)
            new_for_month.append(out_row)
            # a second revision of the same identity within one capture batch
            # (should not happen - the source dump has no duplicate identities
            # within itself - but if it ever does, treat each as its own
            # revision rather than silently keeping only the last)
            existing[row.identity] = out_row
        if new_for_month:
            result[month] = new_for_month
    return result, duplicate_count, revised_count


def raw_month_path(output: Path, month: str) -> Path:
    return output / "raw" / f"{month}.csv"


def append_raw(output: Path, by_month_new_rows: dict[str, list[dict[str, str]]]) -> None:
    for month, new_rows in by_month_new_rows.items():
        path = raw_month_path(output, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS)
            if write_header:
                writer.writeheader()
            for out_row in new_rows:
                writer.writerow({field: out_row.get(field, "") for field in RAW_FIELDS})


# ---- normalized output: sentinels become missing, nothing else changes ------

def normalize_row(row_values: dict[str, str], current_speed_header: str, direction_header: str) -> dict[str, Any]:
    def num_or_blank(field: str) -> str:
        v = row_values.get(field, "")
        return "" if is_sentinel_or_missing(v) else v

    secs = int(row_values["Seconds"])
    utc = datetime.fromtimestamp(secs, tz=timezone.utc)
    return {
        "site": row_values["Site"],
        "site_number": row_values["SiteNumber"],
        "timestamp_utc": utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timestamp_aest": row_values["DateTime"],
        "latitude": row_values.get("Latitude", ""),
        "longitude": row_values.get("Longitude", ""),
        "hs_m": num_or_blank("Hsig"),
        "hmax_m": num_or_blank("Hmax"),
        "tp_s": num_or_blank("Tp"),
        "tz_s": num_or_blank("Tz"),
        "sst_deg_c": num_or_blank("SST"),
        "peak_direction_deg": num_or_blank("Direction"),
        # Current speed/direction: preserved as source values when not a
        # sentinel, with NO unit or "from"/"toward" assumption attached. The
        # source header is literally "Current Speed" / "Current Direction",
        # with no unit or datum hint, so current_speed_unit() and
        # direction_datum() (reused from qld_wave_archive.py - same "claimed,
        # not asserted" pattern, not reimplemented) will read "unstated"
        # until Queensland's header changes to say otherwise. No u/v vector
        # is computed here: that requires both a unit and a from/toward
        # convention, neither of which is confirmed, so it is deferred
        # rather than guessed.
        "current_speed": num_or_blank("Current Speed"),
        "current_speed_unit": archive.current_speed_unit(current_speed_header),
        "current_direction_deg": num_or_blank("Current Direction"),
        "direction_datum": archive.direction_datum(direction_header),
        "revision": row_values.get("_revision", ""),
        "captured_at_utc": row_values.get("_captured_at_utc", ""),
    }


def write_normalized(output: Path, all_raw_rows: list[dict[str, str]]) -> None:
    normalized_dir = output / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    path = normalized_dir / "latest.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=NORMALIZED_FIELDS)
        writer.writeheader()
        for raw_row in sorted(all_raw_rows, key=lambda r: (r["Site"], r["Seconds"], int(r["_revision"]))):
            writer.writerow(normalize_row(raw_row, "Current Speed", "Direction"))


# ---- QC ----------------------------------------------------------------------

def build_qc(
    *,
    retrieved_at_utc: str,
    source_bytes: bytes,
    rows: list[Row],
    schema: dict[str, list[str]],
    duplicate_count: int,
    revised_count: int,
) -> dict[str, Any]:
    by_station: dict[str, dict[str, int]] = {}
    for row in rows:
        entry = by_station.setdefault(row.site, {"rows": 0, "valid_current_rows": 0})
        entry["rows"] += 1
        if valid(row, "Current Speed"):
            entry["valid_current_rows"] += 1

    # Parsed, not string-sorted: at least one station (Abbot Point TriAxys)
    # publishes "YYYY-MM-DD HH:MM:SS" instead of the "T"-separated format
    # everyone else uses, and a space sorts before "T" in ASCII regardless of
    # the actual time of day - a naive string min/max would silently report
    # the wrong earliest/latest timestamp whenever that station is present.
    parsed_stamps = []
    for row in rows:
        raw_dt = row.values.get("DateTime", "")
        if not raw_dt:
            continue
        try:
            parsed_stamps.append((archive.parse_timestamp(raw_dt), raw_dt))
        except ValueError:
            continue
    parsed_stamps.sort(key=lambda pair: pair[0])
    return {
        "retrieved_at_utc": retrieved_at_utc,
        "source_url": SOURCE_URL,
        "resource_id": RESOURCE_ID,
        "resource_page": RESOURCE_PAGE,
        "licence_as_shown": LICENCE_AS_SHOWN,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_bytes": len(source_bytes),
        "total_rows": len(rows),
        "earliest_timestamp_aest": parsed_stamps[0][1] if parsed_stamps else None,
        "latest_timestamp_aest": parsed_stamps[-1][1] if parsed_stamps else None,
        "rows_by_station": {s: v["rows"] for s, v in sorted(by_station.items())},
        "valid_current_rows_by_station": {s: v["valid_current_rows"] for s, v in sorted(by_station.items())},
        "local_stations": {
            s: by_station.get(s, {"rows": 0, "valid_current_rows": 0}) for s in LOCAL_STATIONS
        },
        "duplicate_row_count": duplicate_count,
        "revised_row_count": revised_count,
        "missing_columns": schema["missing"],
        "unexpected_columns": schema["unexpected"],
    }


def write_qc(output: Path, qc: dict[str, Any], stamp: str) -> None:
    qc_dir = output / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    archive.save_json(qc_dir / f"{stamp}.json", qc)
    archive.save_json(qc_dir / "latest.json", qc)


# ---- fail-loud checks ----------------------------------------------------------

def check_fail_conditions(rows: list[Row], schema: dict[str, list[str]], utc_aest_mismatches: list) -> None:
    """Every condition here raises CaptureError with enough detail to act on.
    Called after the QC snapshot has already been written, so a failure is
    never silent and always leaves a record of what was seen."""
    if schema["missing"]:
        raise CaptureError(f"required column(s) missing from source: {schema['missing']}")
    if schema["unexpected"]:
        raise CaptureError(
            f"source schema changed: unexpected column(s) {schema['unexpected']} "
            f"not in the known set {sorted(REQUIRED_COLUMNS + list(KNOWN_EXTRA_COLUMNS))}"
        )
    present_sites = {row.site for row in rows}
    found_locals = [s for s in LOCAL_STATIONS if s in present_sites]
    if not found_locals:
        raise CaptureError(
            f"none of the target local stations {LOCAL_STATIONS} were present in this capture "
            f"(stations seen: {sorted(present_sites)})"
        )
    local_rows = [row for row in rows if row.site in LOCAL_STATIONS]
    if local_rows and not any(valid(row, "Current Speed") for row in local_rows):
        raise CaptureError(
            "every current-speed value at the local stations is a sentinel "
            f"({SENTINEL_THRESHOLD} or below) in this capture - the current sensors "
            "may be down feed-wide, or the column has changed meaning"
        )
    if utc_aest_mismatches:
        examples = utc_aest_mismatches[:5]
        raise CaptureError(
            f"{len(utc_aest_mismatches)} row(s) failed the UTC/AEST consistency check "
            f"(Seconds does not convert to the published DateTime): {examples}"
        )


# ---- orchestration -------------------------------------------------------------

def run_capture(output: Path, data: bytes | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The whole capture, as one function of (output dir, source bytes, clock)
    so tests can supply fixture bytes and a fixed clock without any network
    or filesystem dependency beyond the scratch output dir they pass in."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    retrieved_at_utc = now.isoformat(timespec="seconds").replace("+00:00", "Z")

    source_bytes = data if data is not None else fetch_dump()
    rows, fieldnames = parse_dump(source_bytes)
    schema = verify_schema(fieldnames)
    utc_aest_mismatches = verify_utc_aest(rows) if not schema["missing"] else []

    duplicate_count = revised_count = 0
    by_month: dict[str, list[dict[str, str]]] = {}
    if not schema["missing"]:
        by_month, duplicate_count, revised_count = partition_new_rows(rows, retrieved_at_utc, output)

    qc = build_qc(
        retrieved_at_utc=retrieved_at_utc, source_bytes=source_bytes, rows=rows,
        schema=schema, duplicate_count=duplicate_count, revised_count=revised_count,
    )
    write_qc(output, qc, stamp)

    check_fail_conditions(rows, schema, utc_aest_mismatches)

    append_raw(output, by_month)
    # normalized/latest.csv reflects everything captured across all months on
    # disk, not just this run's new rows - rebuild it from the raw files.
    all_raw = []
    for path in sorted((output / "raw").glob("*.csv")) if (output / "raw").exists() else []:
        with path.open("r", encoding="utf-8", newline="") as handle:
            all_raw.extend(csv.DictReader(handle))
    write_normalized(output, all_raw)

    new_row_count = sum(len(v) for v in by_month.values())
    qc["new_rows_appended"] = new_row_count
    write_qc(output, qc, stamp)
    return qc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/qld-nrt"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        qc = run_capture(args.output)
    except CaptureError as exc:
        print(f"CAPTURE FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(qc, indent=2))
    print(f"new rows appended: {qc['new_rows_appended']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
