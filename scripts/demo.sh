#!/bin/sh
# Illustrative demo on SYNTHETIC fixtures: initial load, change load, idempotent repeat.
set -e
TS=$(date +%Y%m%d%H%M%S)
CFG=config/config.example.yaml
export IRIS_DATA_ORIGIN=fixture
echo "== run 1: snapshot_1 (initial load)";        python -m bess_mastr run --config $CFG --mode replay --input tests/fixtures/mastr_json/snapshot_1 --run-id demo-$TS-r1
echo "== run 2: snapshot_2 (changes)";             python -m bess_mastr run --config $CFG --mode replay --input tests/fixtures/mastr_json/snapshot_2 --run-id demo-$TS-r2
echo "== run 3: snapshot_2 again (must be 0 deltas)"; python -m bess_mastr run --config $CFG --mode replay --input tests/fixtures/mastr_json/snapshot_2 --run-id demo-$TS-r3
