#!/usr/bin/env python3
"""
Driver for golden_check.js's Python-path check. Reads a JSON array of
{"features": {...}, "spot": {...}} from stdin, runs each through
forecast_client.ForecastEngine (the real bridge to forecast-core.js via
node_runner.js), and writes a JSON array of result traces to stdout.

Exists so golden_check.js can prove the Python calibration path actually
agrees with forecast-core.js, instead of assuming it does because
node_runner.js exists. Before this, nothing exercised that path at all.
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forecast_client import ForecastEngine

rows = json.load(sys.stdin)
with ForecastEngine() as fc:
    results = fc.predict_batch([(r["features"], r["spot"]) for r in rows])
json.dump(results, sys.stdout)
