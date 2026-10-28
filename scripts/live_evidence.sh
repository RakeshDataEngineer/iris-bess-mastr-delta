#!/bin/sh
# Observed evidence: two live MaStR runs on a FRESH staging DB (initial + incremental),
# then copy the reports, the delta table and the validation results into examples/.
set -e
cd "$(dirname "$0")/.."
unset IRIS_DATA_ORIGIN
export IRIS_DB_URL="sqlite:///data/live.sqlite"
rm -f data/live.sqlite data/live.iris_staging.sqlite
CFG=config/config.example.yaml
runid() { python -m bess_mastr --log-level WARNING run --config $CFG --mode live \
          | python -c "import json,sys; print(json.load(sys.stdin)['run_id'])"; }
echo "live run 1 (initial)...";     R1=$(runid)
echo "live run 2 (incremental)..."; R2=$(runid)
cp "reports/$R1/quality_report.md" examples/quality_report_live_run1.md
cp "reports/$R2/quality_report.md" examples/quality_report_live_run2.md
python -m bess_mastr --log-level WARNING export-deltas --config $CFG --out examples/live_bess_pipeline_deltas.csv
python scripts/validate.py data/live.sqlite > examples/live_validation_query_results.txt
echo "run1=$R1 run2=$R2"
tail -1 examples/live_validation_query_results.txt
grep -E "deltas written|in_scope_projects|total_power_mw|technology_verified|Quality flags" examples/quality_report_live_run1.md
grep -E "deltas written|unchanged" examples/quality_report_live_run2.md
