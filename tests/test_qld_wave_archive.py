import sqlite3
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import qld_wave_archive as qwa


class TimestampTests(unittest.TestCase):
    def test_aest_is_explicit_and_converts_to_utc(self):
        stamp = qwa.parse_timestamp("2026-01-01T00:30")
        self.assertEqual(stamp.isoformat(timespec="minutes"), "2026-01-01T00:30+10:00")
        self.assertEqual(stamp.astimezone(timezone.utc).isoformat(timespec="minutes"), "2025-12-31T14:30+00:00")

    def test_common_historical_format(self):
        stamp = qwa.parse_timestamp("31/12/2016 23:50")
        self.assertEqual(stamp.year, 2016)
        self.assertEqual(stamp.minute, 50)


class ValueTests(unittest.TestCase):
    def test_sentinel_is_missing(self):
        value, flag = qwa.parse_number("hs_m", "-99.9")
        self.assertEqual(value, "")
        self.assertEqual(flag, "hs_m:sentinel")

    def test_direction_360_wraps_to_zero(self):
        value, flag = qwa.parse_number("peak_direction_deg", "360")
        self.assertEqual(value, "0")
        self.assertIsNone(flag)

    def test_historical_direction_header(self):
        mapping = qwa.map_columns(["Date/Time", "Hs", "Hmax", "Tz", "Tp", "Dir_Tp TRUE", "SST"])
        self.assertEqual(mapping["peak_direction_deg"], "Dir_Tp TRUE")


class DatumTests(unittest.TestCase):
    def test_datum_is_recorded_not_assumed(self):
        self.assertEqual(qwa.direction_datum("Dir_Tp TRUE"), "true_claimed")
        self.assertEqual(qwa.direction_datum("Peak Direction (degrees)"), "unstated")
        self.assertEqual(qwa.direction_datum("Current Direction (degrees magnetic north)"), "magnetic_claimed")
        self.assertEqual(qwa.direction_datum(None), "absent")

    def test_current_speed_unit_is_recorded_not_assumed(self):
        self.assertEqual(qwa.current_speed_unit("Current speed (Knots)"), "knots_claimed")
        self.assertEqual(qwa.current_speed_unit("Current Speed (m/s)"), "ms_claimed")
        self.assertEqual(qwa.current_speed_unit("Current Speed"), "unstated")


class ColumnGuardTests(unittest.TestCase):
    def test_timestamp_only_resource_is_rejected(self):
        with self.assertRaises(qwa.ArchiveError):
            qwa.map_columns(["Date/Time", "Battery", "Comment"])

    def test_current_only_resource_is_captured_not_discarded(self):
        mapping = qwa.map_columns(["Date/Time", "Current Speed (m/s)", "Current Direction (degrees)"])
        self.assertEqual(mapping["current_speed"], "Current Speed (m/s)")
        self.assertEqual(mapping["current_direction_deg"], "Current Direction (degrees)")


class ResourceTests(unittest.TestCase):
    def test_combined_resource_matches_requested_year(self):
        resource = {
            "name": "Wave data - 1995 to 2011",
            "format": "CSV",
            "url": "https://example.test/archive.csv",
        }
        self.assertTrue(qwa.resource_is_selected(resource, {2004}))
        self.assertFalse(qwa.resource_is_selected(resource, {2012}))


def make_resource(root, raw, name="Wave data - 2026", year=2026, mk4=0, resource_id="resource"):
    return qwa.Resource(
        station="tweed-offshore",
        buoy_depth_m=60,
        dataset_id="dataset",
        dataset_slug="slug",
        dataset_license="CC-BY-ND-4.0",
        resource_id=resource_id,
        resource_name=name,
        resource_url="https://example.test/source.csv",
        resource_modified="2026-02-01T00:00:00",
        resource_position=0,
        resource_year=year,
        is_mk4=mk4,
        datastore_active=True,
        datastore_complete=True,
        raw_path=raw,
    )


class ParseTests(unittest.TestCase):
    def test_parse_modern_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "source.csv"
            raw.write_text(
                "Date/Time (AEST),Hs (m),Hmax (m),Tz (s),Tp (s),Peak Direction (degrees),SST (degrees C)\n"
                "2026-01-01T00:00,1.2,2.1,5.0,8.0,120.0,25.0\n",
                encoding="utf-8",
            )
            connection = qwa.create_database(root / "build.sqlite")
            qc = qwa.parse_resource(connection, make_resource(root, raw))
            row = connection.execute(
                "SELECT timestamp_utc,hs_m,direction_datum,value_count FROM rows"
            ).fetchone()
            connection.close()
            self.assertEqual(qc["parsed_rows"], 1)
            self.assertEqual(qc["empty_value_rows"], 0)
            self.assertEqual(row, ("2025-12-31T14:00Z", "1.2", "unstated", 6))


class DuplicateTests(unittest.TestCase):
    def insert(self, connection, resource_id, name, year, mk4, position, values):
        connection.execute(
            "INSERT INTO rows VALUES (" + ",".join("?" * 23) + ")",
            (
                "tweed-heads", 22, "2026-01-01T00:00+10:00", "2025-12-31T14:00Z",
                *values,
                "", resource_id, name, year, "2026-02-01", position, mk4, 10,
            ),
        )

    def test_newer_year_wins_boundary_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            connection = qwa.create_database(Path(temporary) / "build.sqlite")
            wave = ("1", "2", "5", "8", "100", "unstated", "25", "", "absent", "", 6)
            self.insert(connection, "old", "Wave data - 2025", 2025, 1, 1, wave)
            self.insert(connection, "new", "Wave data - 2026", 2026, 1, 2, wave)
            sql, params = qwa.selected_rows_sql("tweed-heads")
            chosen = connection.execute(sql, params).fetchone()
            connection.close()
            self.assertEqual(chosen[16], "new")

    def test_blank_row_never_outranks_a_row_with_values(self):
        """A current-only resource must not evict wave data for the same timestamp."""
        with tempfile.TemporaryDirectory() as temporary:
            connection = qwa.create_database(Path(temporary) / "build.sqlite")
            wave = ("1", "2", "5", "8", "100", "unstated", "25", "", "absent", "", 6)
            blank = ("", "", "", "", "", "absent", "", "", "absent", "", 0)
            self.insert(connection, "waves", "Wave data - 2020", 2020, 0, 1, wave)
            self.insert(connection, "currents", "Current data - 2026 Mk4", 2026, 1, 2, blank)
            sql, params = qwa.selected_rows_sql("tweed-heads")
            chosen = connection.execute(sql, params).fetchone()
            connection.close()
            self.assertEqual(chosen[16], "waves")


if __name__ == "__main__":
    unittest.main()


class ExclusionTests(unittest.TestCase):
    def test_dead_compass_window_nulls_direction_only(self):
        from datetime import datetime
        inside = datetime(2023, 8, 15, tzinfo=qwa.AEST)
        outside = datetime(2023, 11, 15, tzinfo=qwa.AEST)
        hits = qwa.exclusions_for("tweed-heads", inside)
        self.assertEqual([h["reason"] for h in hits], ["compass_dead"])
        self.assertEqual(hits[0]["fields"], ["peak_direction_deg"])
        self.assertEqual(qwa.exclusions_for("tweed-heads", outside), [])
        self.assertEqual(qwa.exclusions_for("gold-coast", inside), [])

    def test_tp_formats_collapse_to_one_bin(self):
        self.assertEqual(qwa.canonical_tp("22.222"), qwa.canonical_tp("22.22"))
        self.assertEqual(qwa.canonical_tp(""), "")


class DateOrderTests(unittest.TestCase):
    def test_order_is_inferred_from_evidence(self):
        self.assertEqual(qwa.infer_date_order(["01/12/2017 10:00", "31/12/2017 10:00"]), "dmy")
        self.assertEqual(qwa.infer_date_order(["01/12/2017 10:00", "12/31/2017 10:00"]), "mdy")
        self.assertEqual(qwa.infer_date_order(["2017-12-01T10:00"]), "none")

    def test_all_ambiguous_resource_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            qwa.infer_date_order(["01/12/2017 10:00", "02/03/2017 10:00"])

    def test_month_day_file_parses_every_day(self):
        """The Gold Coast 2017-2018 archives use month/day. Parsed as day/month
        this rejects every day above 12 and misdates the rest."""
        self.assertEqual(qwa.parse_timestamp("12/31/2016 23:50", "mdy").date().isoformat(), "2016-12-31")
        self.assertEqual(qwa.parse_timestamp("01/12/2017 10:00", "mdy").date().isoformat(), "2017-01-12")
        self.assertEqual(qwa.parse_timestamp("01/12/2017 10:00", "dmy").date().isoformat(), "2017-12-01")
