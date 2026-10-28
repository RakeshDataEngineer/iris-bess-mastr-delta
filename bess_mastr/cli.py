"""Command line interface.

    python -m bess_mastr run    --config config/config.yaml --mode live
    python -m bess_mastr run    --config ... --mode replay --input data/landing/mastr_json/<run_id>
    python -m bess_mastr run    --config ... --mode bulk   --input /path/to/unzipped_export
    python -m bess_mastr init-db --config ...
    python -m bess_mastr export-deltas --config ... --out examples/deltas.csv [--run-id ...]
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigError, load_config
from .logging_setup import configure_logging
from .pipeline import Pipeline
from .report import export_deltas_csv, write_report
from .sources.mastr_json import SourceError
from .store import Database


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bess_mastr", description="MaStR >10 MW battery delta pipeline (staging only)")
    ap.add_argument("--log-level", default="INFO")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="extract + stage deltas")
    run.add_argument("--config", required=True)
    run.add_argument("--mode", choices=["live", "replay", "bulk"], default="live")
    run.add_argument("--input", action="append", help="replay: landed page dir; bulk: XML dir/files (repeatable)")
    run.add_argument("--run-id")
    run.add_argument("--full-refresh", action="store_true", help="live: ignore the watermark")

    init = sub.add_parser("init-db", help="create iris_staging tables")
    init.add_argument("--config", required=True)

    exp = sub.add_parser("export-deltas", help="write deltas to CSV")
    exp.add_argument("--config", required=True)
    exp.add_argument("--out", required=True)
    exp.add_argument("--run-id")

    args = ap.parse_args(argv)
    configure_logging(args.log_level)
    if args.cmd == "run" and args.mode in {"replay", "bulk"} and not args.input:
        ap.error(f"--input is required for --mode {args.mode}")
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    db = Database(cfg.db)
    try:
        if args.cmd == "init-db":
            db.ensure_schema()
            print(f"schema {cfg.db.schema} ready")
            return 0
        if args.cmd == "export-deltas":
            n = export_deltas_csv(db, args.out, args.run_id)
            print(f"wrote {n} deltas to {args.out}")
            return 0
        summary = Pipeline(cfg, db).run(args.mode, args.input, args.run_id, args.full_refresh)
        out = write_report(db, summary, cfg.reports_dir, cfg.scope.threshold_mw)
        print(json.dumps({"run_id": summary.run_id, "status": summary.status, "deltas": dict(summary.deltas),
                          "rejected": summary.records_rejected, "unchanged": summary.unchanged,
                          "stale_skipped": summary.stale_skipped, "report": str(out)}, indent=2))
        return 0
    except SourceError as exc:
        print(f"source error (run marked failed, watermark unchanged): {exc}", file=sys.stderr)
        return 3
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
