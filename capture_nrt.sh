#!/bin/bash
URL="https://www.data.qld.gov.au/datastore/dump/2bbef99e-9974-49b9-a316-57402b00609c?bom=true"
OUT=~/visproject/data/qld-nrt/raw/wave-7dayop-$(date +%Y%m%d-%H%M).csv
curl -sSf --retry 3 --retry-delay 30 -o "$OUT" "$URL"
if [ $? -ne 0 ] || [ ! -s "$OUT" ]; then
  rm -f "$OUT"
  echo "$(date): capture failed" >> ~/visproject/data/qld-nrt/raw/capture.log
else
  echo "$(date): $(wc -l < "$OUT") lines" >> ~/visproject/data/qld-nrt/raw/capture.log
fi
