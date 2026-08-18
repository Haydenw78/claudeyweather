#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PROJECT_DIR}/.venv-copernicus/bin/python"
CAPTURE_SCRIPT="${PROJECT_DIR}/capture_freedive_glorys_forecast.py"
OUTPUT_DIR="${PROJECT_DIR}/data/freedive-gc/glorys-forecast"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  print -u2 "Copernicus Python environment not found: ${PYTHON_BIN}"
  exit 1
fi

if [[ ! -f "${CAPTURE_SCRIPT}" ]]; then
  print -u2 "Forecast capture script not found: ${CAPTURE_SCRIPT}"
  exit 1
fi

cd "${PROJECT_DIR}"
exec "${PYTHON_BIN}" "${CAPTURE_SCRIPT}" --output "${OUTPUT_DIR}"
