"""
forecast_client.py

Python's access to the canonical forecast module. Python keeps responsibility
for data preparation, folds, optimisation and statistics. It does not
reimplement the forecast: every prediction comes back from forecast-core.js
through a single long-lived Node process.

This replaces python_calibration_engine.py, which is now an archived
diagnostic only. Do not import that file for anything that ships.

Usage:

    from forecast_client import ForecastEngine

    with ForecastEngine() as fc:
        out = fc.predict_batch(rows)          # rows: [(features, spot), ...]
        # each out[i] is the full component trace, not just a number

Weights can be varied per call for calibration:

    out = fc.predict_batch(rows, weights={"wStir": 30, "wWind": 24})
"""

import json
import os
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "node_runner.js"


class ForecastEngineError(RuntimeError):
    pass


class ForecastEngine:
    """One Node process, kept alive. Spawning per row is too slow for nulls."""

    def __init__(self, runner=RUNNER, node="node"):
        if not Path(runner).exists():
            raise ForecastEngineError(f"runner not found: {runner}")
        self.proc = subprocess.Popen(
            [node, str(runner)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._constants = None

    def constants(self):
        """The live WEIGHTS and BED tables from forecast-core.js, fetched
        once and cached. Use this to build a full weights dict for
        predict_batch(weights=...) overrides - predict() replaces WEIGHTS
        wholesale rather than merging, so a partial override needs a base:

            base = dict(fc.constants()['WEIGHTS'])
            variant = dict(base, wStir=30)
        """
        if self._constants is None:
            self.proc.stdin.write(json.dumps({"id": "constants", "cmd": "constants"}) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                err = self.proc.stderr.read()
                raise ForecastEngineError(f"node runner died: {err.strip()}")
            msg = json.loads(line)
            if not msg.get("ok"):
                raise ForecastEngineError(f"constants: {msg.get('error')}")
            self._constants = msg["result"]
        return self._constants

    def predict_batch(self, rows, weights=None):
        """rows: iterable of (features dict, spot dict). Returns list of traces.

        Order is preserved. A row that errors raises rather than returning a
        silent default, because a silently defaulted row in a calibration fold
        is worse than a crash.
        """
        rows = list(rows)
        if not rows:
            return []

        payload = []
        for i, (features, spot) in enumerate(rows):
            req = {"id": i, "features": features, "spot": spot}
            if weights:
                req["weights"] = weights
            payload.append(json.dumps(req, allow_nan=False))

        self.proc.stdin.write("\n".join(payload) + "\n")
        self.proc.stdin.flush()

        out = [None] * len(rows)
        for _ in range(len(rows)):
            line = self.proc.stdout.readline()
            if not line:
                err = self.proc.stderr.read()
                raise ForecastEngineError(f"node runner died: {err.strip()}")
            msg = json.loads(line)
            if not msg.get("ok"):
                raise ForecastEngineError(
                    f"row {msg.get('id')}: {msg.get('error')}")
            out[msg["id"]] = msg["result"]
        return out

    def predict(self, features, spot, weights=None):
        return self.predict_batch([(features, spot)], weights)[0]

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            self.proc.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


STIRLAG_MISSING_NOTE = (
    "stirLag has never been present in any export, so the 34-point stir "
    "term has never actually been fitted."
)


def row_from_export(rec, season_adj=0.0):
    """Map a row of an exported observation file to (features, spot).

    Returns None if the record carries no stirLag, rather than substituting
    a proxy. stirLag is a 48h settling-weighted average of bed stress and
    cannot be reconstructed from a point-in-time export. The historical
    Python engine fell back to min(1.6, ubMs/0.35) for every such row, which
    silently treats a term that is itself usually saturated at the 1.6 cap
    as if it varied meaningfully. A caller with no stirLag should refuse the
    row and count it, not fit against a degenerate proxy. See
    STIRLAG_MISSING_NOTE.
    """
    stir = rec.get("stirLag")
    if stir is None:
        return None
    features = {
        "stirLag": stir,
        # the app feeds gust when present, sustained otherwise
        "mixKmh": rec.get("gustKmh") if rec.get("gustKmh") is not None else rec.get("windKmh"),
        "ekman": rec.get("ekman") or 0.0,
        "sstAnom": rec.get("sstAnom") or 0.0,
        "rain72": rec.get("rain72mm") or 0.0,
        "season": season_adj,
    }
    spot = {
        "type": rec.get("type") or "shelf",
        "offshoreKm": rec.get("offshoreKm"),
        "vMin": rec.get("vMin"),
        "vMax": rec.get("vMax"),
    }
    return features, spot


def rows_from_export(recs, season=None):
    """Convert a list of export records to (features, spot) rows.

    Refuses (skips) any record with no stirLag. Returns (rows, kept_records,
    refused_count) so a caller can report exactly how many rows it lost and
    why, rather than silently fitting on whatever remained.
    """
    rows, kept = [], []
    refused = 0
    for r in recs:
        adj = (season or {}).get(r.get("month"), 0.0)
        fs = row_from_export(r, season_adj=adj)
        if fs is None:
            refused += 1
            continue
        rows.append(fs)
        kept.append(r)
    return rows, kept, refused
