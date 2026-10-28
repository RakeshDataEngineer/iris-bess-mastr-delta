-- Validation queries for iris_staging (portable: PostgreSQL and SQLite).
-- Run with:  python scripts/validate.py <db-url-or-sqlite-path>
-- Checks marked "expect 0" are acceptance gates.

-- name: result_large_batteries_current
SELECT mastr_id, unit_name, scope_power_mw, power_basis, usable_capacity_mwh, duration_h,
       status, federal_state, latitude, longitude, quality_flags, data_origin
FROM iris_staging.v_bess_large_battery_current
ORDER BY scope_power_mw DESC, mastr_id;

-- name: deltas_by_run_and_type
SELECT run_id, change_type, COUNT(*) AS n
FROM iris_staging.bess_pipeline_deltas
GROUP BY run_id, change_type
ORDER BY run_id, change_type;

-- name: change_history_example
SELECT run_id, mastr_id, change_type, changed_fields
FROM iris_staging.bess_pipeline_deltas
WHERE mastr_id IN ('SEE900000000001', 'SEE900000000003')
ORDER BY mastr_id, detected_at, run_id;

-- name: gate_in_scope_at_or_below_threshold  (expect 0)
SELECT COUNT(*) AS violations
FROM iris_staging.bess_projects_current
WHERE in_scope AND scope_power_mw <= 10;

-- name: gate_out_of_scope_above_threshold_battery  (expect 0)
SELECT COUNT(*) AS violations
FROM iris_staging.bess_projects_current
WHERE NOT in_scope AND scope_power_mw > 10 AND scope_reason = 'battery_above_threshold';

-- name: gate_duplicate_transitions  (expect 0)
SELECT COUNT(*) AS violations FROM (
  SELECT mastr_id, previous_record_hash, record_hash
  FROM iris_staging.bess_pipeline_deltas
  GROUP BY mastr_id, previous_record_hash, record_hash
  HAVING COUNT(*) > 1
) d;

-- name: gate_current_state_traceable_to_delta  (expect 0)
SELECT COUNT(*) AS violations
FROM iris_staging.bess_projects_current c
WHERE c.in_scope
  AND NOT EXISTS (SELECT 1 FROM iris_staging.bess_pipeline_deltas d
                  WHERE d.mastr_id = c.mastr_id AND d.record_hash = c.record_hash);

-- name: gate_duplicate_mastr_ids_in_current  (expect 0)
SELECT COUNT(*) AS violations FROM (
  SELECT mastr_id FROM iris_staging.bess_projects_current GROUP BY mastr_id HAVING COUNT(*) > 1
) d;

-- name: rejected_by_reason
SELECT reason_code, COUNT(*) AS n
FROM iris_staging.bess_rejected_records
GROUP BY reason_code
ORDER BY reason_code;

-- name: run_log
SELECT run_id, mode, status, data_origin, records_seen, records_valid, records_rejected,
       duplicates_in_batch, stale_skipped, unchanged, deltas_written, watermark_after
FROM iris_staging.pipeline_runs
ORDER BY run_id;
