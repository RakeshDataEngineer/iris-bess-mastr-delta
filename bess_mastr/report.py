"""Quality report: run counters + coverage of the staged in-scope population."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .pipeline import RunSummary
from .store import Database


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def build_report(db: Database, summary: RunSummary, threshold: Decimal) -> dict:
    cur = db.query(f"SELECT * FROM {db.t('bess_projects_current')} WHERE in_scope")
    total = len(cur)

    def share(pred) -> str:
        return f"{(100 * sum(1 for r in cur if pred(r)) / total):.1f}%" if total else "n/a"

    by_status, by_state, flags = {}, {}, {}
    for r in cur:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        st = r["federal_state"] or "unknown"
        by_state[st] = by_state.get(st, 0) + 1
        for f in filter(None, (r["quality_flags"] or "").split(",")):
            flags[f] = flags.get(f, 0) + 1

    rejected = db.query(
        f"SELECT reason_code, COUNT(*) AS n FROM {db.t('bess_rejected_records')} GROUP BY reason_code ORDER BY reason_code")
    delta_hist = db.query(
        f"SELECT change_type, COUNT(*) AS n FROM {db.t(db.cfg.deltas_table)} GROUP BY change_type ORDER BY change_type")
    dup_check = db.query(
        f"SELECT COUNT(*) AS n FROM (SELECT mastr_id, previous_record_hash, record_hash "
        f"FROM {db.t(db.cfg.deltas_table)} GROUP BY mastr_id, previous_record_hash, record_hash "
        f"HAVING COUNT(*) > 1) d")[0]["n"]
    threshold_violations = sum(1 for r in cur if _d(r["scope_power_mw"]) <= threshold)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_origin": summary.data_origin,
        "run": summary.as_dict(),
        "coverage": {
            "in_scope_projects": total,
            "total_power_mw": str(sum((_d(r["scope_power_mw"]) for r in cur), Decimal(0))),
            "total_capacity_mwh_known": str(sum((_d(r["usable_capacity_mwh"]) for r in cur), Decimal(0))),
            "with_capacity_mwh": share(lambda r: r["usable_capacity_mwh"] is not None),
            "with_valid_coordinates": share(lambda r: r["location_quality"] == "ok"),
            "with_net_power_basis": share(lambda r: r["power_basis"] == "net"),
            "technology_verified_battery": share(lambda r: bool(r["is_battery"])),
            "by_status": dict(sorted(by_status.items())),
            "by_federal_state": dict(sorted(by_state.items())),
            "quality_flags_in_scope": dict(sorted(flags.items())),
        },
        "rejections_all_time": {r["reason_code"]: r["n"] for r in rejected},
        "deltas_all_time": {r["change_type"]: r["n"] for r in delta_hist},
        "acceptance_checks": {
            "duplicate_delta_transitions": dup_check,
            "in_scope_rows_at_or_below_threshold": threshold_violations,
        },
    }


def to_markdown(rep: dict) -> str:
    run, cov = rep["run"], rep["coverage"]
    origin_note = ("**Data origin: FIXTURE (synthetic, illustrative - not observational MaStR data).**"
                   if rep["data_origin"] == "fixture" else "**Data origin: OBSERVED (live MaStR extraction).**")
    lines = [
        f"# Quality report - run `{run['run_id']}`", "", origin_note, "",
        f"Mode `{run['mode']}`, status **{run['status']}**, generated {rep['generated_at']}.", "",
        "## This run", "",
        "| metric | value |", "|---|---|",
        f"| pages | {run['pages']} |",
        f"| records seen | {run['records_seen']} |",
        f"| valid (after de-dup) | {run['records_valid']} |",
        f"| rejected | {run['records_rejected']} |",
        f"| exact duplicates dropped | {run['duplicates_exact']} |",
        f"| conflicting duplicates superseded | {run['duplicates_superseded']} |",
        f"| stale (older than stored) skipped | {run['stale_skipped']} |",
        f"| unchanged | {run['unchanged']} |",
        f"| deltas written | {run['deltas_written']} {run['deltas'] or ''} |",
        f"| since filter / watermark after | {run['since_filter']} / {run['watermark_after']} |", "",
        "Rejections this run: " + (", ".join(f"{k}={v}" for k, v in sorted(run["rejections"].items())) or "none"), "",
        "## Coverage of staged >10 MW battery population", "",
        "| metric | value |", "|---|---|",
    ]
    for k in ("in_scope_projects", "total_power_mw", "total_capacity_mwh_known", "with_capacity_mwh",
              "with_valid_coordinates", "with_net_power_basis", "technology_verified_battery"):
        lines.append(f"| {k} | {cov[k]} |")
    lines += ["", f"By status: {cov['by_status']}", "", f"By federal state: {cov['by_federal_state']}", "",
              f"Quality flags (in-scope): {cov['quality_flags_in_scope']}", "",
              "## Acceptance checks", "",
              f"- duplicate delta transitions (must be 0): **{rep['acceptance_checks']['duplicate_delta_transitions']}**",
              f"- in-scope rows with power <= threshold (must be 0): "
              f"**{rep['acceptance_checks']['in_scope_rows_at_or_below_threshold']}**",
              f"- deltas all-time: {rep['deltas_all_time']}",
              f"- rejections all-time: {rep['rejections_all_time']}", ""]
    return "\n".join(lines)


def write_report(db: Database, summary: RunSummary, out_dir: str | Path, threshold: Decimal) -> Path:
    rep = build_report(db, summary, threshold)
    out = Path(out_dir) / summary.run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "quality_report.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    (out / "quality_report.md").write_text(to_markdown(rep), encoding="utf-8")
    return out


def export_deltas_csv(db: Database, path: str | Path, run_id: str | None = None) -> int:
    sql = (f"SELECT run_id, mastr_id, change_type, unit_name, scope_power_mw, net_power_mw, usable_capacity_mwh, "
           f"status, federal_state, latitude, longitude, source_updated_at, changed_fields, data_origin "
           f"FROM {db.t(db.cfg.deltas_table)}")
    params: tuple = ()
    if run_id:
        sql += " WHERE run_id = ?"
        params = (run_id,)
    rows = db.query(sql + " ORDER BY run_id, mastr_id, change_type", params)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(rows[0].keys() if rows else ["run_id"])
        for r in rows:
            w.writerow([json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for v in r.values()])
    return len(rows)
