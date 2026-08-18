"""Fixture-based tests for qld_nrt_capture.py.

No network access. Every test builds a small synthetic DataStore-dump CSV in
memory (same columns, same format, as the real feed - verified against a live
sample fetched 2026-08-18) and runs it through run_capture() with a fixed
clock and an isolated tmp_path output directory.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

import qld_nrt_capture as cap

COLUMNS = ["Site", "SiteNumber", "Seconds", "DateTime", "Latitude", "Longitude",
           "Hsig", "Hmax", "Tp", "Tz", "SST", "Direction", "Current Speed", "Current Direction"]

BASE = {
    "Site": "Bilinga", "SiteNumber": "4224", "Seconds": "1786370400", "DateTime": "2026-08-11T00:00:00",
    "Latitude": "-28.14177", "Longitude": "153.51399", "Hsig": "0.850", "Hmax": "1.400", "Tp": "9.520",
    "Tz": "4.740", "SST": "20.07", "Direction": "69.80", "Current Speed": "0.05", "Current Direction": "92.13",
}


def make_row(**overrides) -> dict:
    row = dict(BASE)
    row.update(overrides)
    return row


def to_csv_bytes(rows: list[dict], columns: list[str] = COLUMNS, bom: bool = True) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    text = buf.getvalue()
    return ("﻿" + text).encode("utf-8") if bom else text.encode("utf-8")


NOW = datetime(2026, 8, 18, 1, 0, 0, tzinfo=timezone.utc)


def seconds_for_aest(aest_iso: str) -> str:
    """Helper: compute the correct Seconds value for a given AEST DateTime
    string, so fixtures that are SUPPOSED to be consistent don't have to have
    their Unix timestamp hand-computed and silently drift from the DateTime
    string next to it."""
    from datetime import datetime as dt
    naive = dt.fromisoformat(aest_iso)
    aware = naive.replace(tzinfo=cap.archive.AEST)
    return str(int(aware.timestamp()))


# ---- identical overlap: repeated identical rows must not be appended --------

def test_identical_overlap_not_appended(tmp_path):
    row = make_row()
    data = to_csv_bytes([row])
    out = tmp_path / "out"

    qc1 = cap.run_capture(out, data=data, now=NOW)
    assert qc1["new_rows_appended"] == 1
    assert qc1["duplicate_row_count"] == 0

    # same bytes fetched again on a later run
    later = datetime(2026, 8, 18, 1, 30, 0, tzinfo=timezone.utc)
    qc2 = cap.run_capture(out, data=data, now=later)
    assert qc2["new_rows_appended"] == 0
    assert qc2["duplicate_row_count"] == 1

    raw_path = cap.raw_month_path(out, "2026-08")
    with raw_path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1, "identical repeat must not be appended a second time"
    assert rows[0]["_revision"] == "1"


# ---- revised row with the same identity: retained, never overwritten -------

def test_revised_row_retains_both_versions(tmp_path):
    out = tmp_path / "out"
    row_v1 = make_row(Hsig="0.850")
    cap.run_capture(out, data=to_csv_bytes([row_v1]), now=NOW)

    later = datetime(2026, 8, 18, 2, 0, 0, tzinfo=timezone.utc)
    row_v2 = make_row(Hsig="0.910")  # same identity (SiteNumber+Seconds), value revised
    qc2 = cap.run_capture(out, data=to_csv_bytes([row_v2]), now=later)

    assert qc2["new_rows_appended"] == 1
    assert qc2["revised_row_count"] == 1
    assert qc2["duplicate_row_count"] == 0

    raw_path = cap.raw_month_path(out, "2026-08")
    with raw_path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2, "both the original and the revised row must be retained"
    assert {r["_revision"] for r in rows} == {"1", "2"}
    assert {r["Hsig"] for r in rows} == {"0.850", "0.910"}
    v1 = next(r for r in rows if r["_revision"] == "1")
    v2 = next(r for r in rows if r["_revision"] == "2")
    assert v1["_captured_at_utc"] != v2["_captured_at_utc"], \
        "each version must record when it was first captured"
    assert v1["_captured_at_utc"] < v2["_captured_at_utc"]


# ---- sentinel current values: preserved raw, blanked only when normalized --

def test_sentinel_preserved_raw_blanked_normalized(tmp_path):
    out = tmp_path / "out"
    row = make_row(**{"Current Speed": "-99.90", "Current Direction": "-99.90"})
    # a companion row with valid local-station current, so this test isolates
    # sentinel-preservation behaviour from the separate
    # all-local-current-is-sentinel fail condition (covered on its own below)
    companion = make_row(Site="Tweed Offshore", SiteNumber="2487",
                          **{"Current Speed": "0.49", "Current Direction": "148.92"})
    cap.run_capture(out, data=to_csv_bytes([row, companion]), now=NOW)

    raw_path = cap.raw_month_path(out, "2026-08")
    with raw_path.open() as f:
        raw_rows = list(csv.DictReader(f))
    assert raw_rows[0]["Current Speed"] == "-99.90"
    assert raw_rows[0]["Current Direction"] == "-99.90"

    normalized_path = out / "normalized" / "latest.csv"
    with normalized_path.open() as f:
        norm_rows = list(csv.DictReader(f))
    assert norm_rows[0]["current_speed"] == ""
    assert norm_rows[0]["current_direction_deg"] == ""


# ---- missing required column: fail loudly ------------------------------------

def test_missing_required_column_fails_loudly(tmp_path):
    columns_missing_hsig = [c for c in COLUMNS if c != "Hsig"]
    row = make_row()
    data = to_csv_bytes([row], columns=columns_missing_hsig)
    out = tmp_path / "out"

    with pytest.raises(cap.CaptureError, match="Hsig"):
        cap.run_capture(out, data=data, now=NOW)

    # QC must still be written, even on failure
    assert (out / "qc" / "latest.json").exists()
    # and nothing may have been appended
    assert not (out / "raw").exists() or not list((out / "raw").glob("*.csv"))


# ---- unexpected station-name suffix: exact match only, never fuzzy ----------

def test_unexpected_station_suffix_not_recognised_as_local(tmp_path):
    # every LOCAL_STATIONS name renamed with an unexpected suffix
    rows = [make_row(Site=name + " (new)", SiteNumber=str(4200 + i))
            for i, name in enumerate(cap.LOCAL_STATIONS)]
    data = to_csv_bytes(rows)
    out = tmp_path / "out"

    with pytest.raises(cap.CaptureError, match="none of the target local stations"):
        cap.run_capture(out, data=data, now=NOW)


# ---- UTC/AEST mismatch: fail loudly ------------------------------------------

def test_utc_aest_mismatch_fails_loudly(tmp_path):
    # Seconds says one instant, DateTime claims a different one
    bad_row = make_row(Seconds="1786370400", DateTime="2026-08-11T05:00:00")
    data = to_csv_bytes([bad_row])
    out = tmp_path / "out"

    with pytest.raises(cap.CaptureError, match="UTC/AEST consistency"):
        cap.run_capture(out, data=data, now=NOW)


def test_utc_aest_tolerates_space_separated_format(tmp_path):
    """Real finding from a live sample (2026-08-18): Abbot Point TriAxys
    publishes "YYYY-MM-DD HH:MM:SS" (a space, not "T") while every other
    station uses "T". That is a formatting difference, not a timestamp
    error, and must not fail the check."""
    aest = "2026-08-11 00:00:00"
    row = make_row(Site="Abbot Point TriAxys", SiteNumber="4805tx",
                    Seconds=seconds_for_aest("2026-08-11T00:00:00"), DateTime=aest)
    data = to_csv_bytes([row])
    out = tmp_path / "out"
    # would raise "no target local stations" if it got past the UTC/AEST
    # check, since Abbot Point is not a local station - so pair it with a
    # genuine local-station row to isolate what we are actually testing
    local_row = make_row()
    qc = cap.run_capture(out, data=to_csv_bytes([row, local_row]), now=NOW)
    assert qc["new_rows_appended"] == 2


# ---- branch where valid current values genuinely exist ----------------------

def test_valid_current_values_counted_by_station(tmp_path):
    rows = [make_row(Site=name, SiteNumber=str(5000 + i), **{"Current Speed": "0.42", "Current Direction": "180.0"})
            for i, name in enumerate(cap.LOCAL_STATIONS)]
    data = to_csv_bytes(rows)
    out = tmp_path / "out"
    qc = cap.run_capture(out, data=data, now=NOW)

    for name in cap.LOCAL_STATIONS:
        assert qc["local_stations"][name]["valid_current_rows"] == 1
        assert qc["local_stations"][name]["rows"] == 1


def test_all_sentinel_current_at_local_stations_fails_loudly(tmp_path):
    rows = [make_row(Site=name, SiteNumber=str(5000 + i), **{"Current Speed": "-99.90", "Current Direction": "-99.90"})
            for i, name in enumerate(cap.LOCAL_STATIONS)]
    data = to_csv_bytes(rows)
    out = tmp_path / "out"
    with pytest.raises(cap.CaptureError, match="every current-speed value"):
        cap.run_capture(out, data=data, now=NOW)


# ---- schema change: unexpected new column also fails loudly -----------------

def test_unexpected_new_column_fails_loudly(tmp_path):
    columns = COLUMNS + ["Water Level"]
    row = make_row()
    data = to_csv_bytes([row], columns=columns)
    out = tmp_path / "out"
    with pytest.raises(cap.CaptureError, match="unexpected column"):
        cap.run_capture(out, data=data, now=NOW)


# ---- capture is not limited to the five named local stations ----------------

def test_captures_stations_beyond_the_local_five(tmp_path):
    rows = [make_row(), make_row(Site="Mackay Mk4", SiteNumber="4740", Seconds=seconds_for_aest("2026-08-11T00:00:00"))]
    data = to_csv_bytes(rows)
    out = tmp_path / "out"
    qc = cap.run_capture(out, data=data, now=NOW)
    assert "Mackay Mk4" in qc["rows_by_station"]
    assert qc["new_rows_appended"] == 2
