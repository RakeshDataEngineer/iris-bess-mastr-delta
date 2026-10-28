#!/bin/sh
# Regenerates examples/ from the SYNTHETIC fixtures (illustrative output, not observational data).
set -e
cd "$(dirname "$0")/.."
WORK=$(mktemp -d)
export IRIS_DB_URL="sqlite:///$WORK/staging.sqlite" IRIS_DATA_ORIGIN=fixture
CFG=config/config.example.yaml
run() { python -m bess_mastr --log-level WARNING run --config $CFG --mode "$1" --input "$2" --run-id "$3"; }
{
  echo "# run 1 - snapshot_1 (initial load)";   run replay tests/fixtures/mastr_json/snapshot_1 ex-r1
  echo "# run 2 - snapshot_2 (changes)";        run replay tests/fixtures/mastr_json/snapshot_2 ex-r2
  echo "# run 3 - snapshot_2 again";            run replay tests/fixtures/mastr_json/snapshot_2 ex-r3
  echo "# run 4 - snapshot_1 again (stale)";    run replay tests/fixtures/mastr_json/snapshot_1 ex-r4
} > examples/run_log.txt 2> "$WORK/stderr.log"
sed -E 's#"report": "[^"]*/(ex-r[0-9])"#"report": "reports/\1"#' examples/run_log.txt > "$WORK/x" && mv "$WORK/x" examples/run_log.txt
python -m bess_mastr --log-level WARNING export-deltas --config $CFG --out examples/bess_pipeline_deltas.csv >/dev/null
cp reports/ex-r2/quality_report.md examples/quality_report_run2.md
cp reports/ex-r2/quality_report.json examples/quality_report_run2.json
python scripts/validate.py "$WORK/staging.sqlite" > examples/validation_query_results.txt
rm -rf reports/ex-r1 reports/ex-r2 reports/ex-r3 reports/ex-r4 "$WORK"
echo "examples/ regenerated"
