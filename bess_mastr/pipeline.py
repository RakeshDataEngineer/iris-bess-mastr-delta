"""Orchestration: extract -> land -> normalise -> de-duplicate -> diff -> stage."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

import requests

from . import __version__
from .config import AppConfig
from .logging_setup import get_logger
from .models import BUSINESS_FIELDS, NormalizedRecord, RejectedRecord
from .normalize import Normalizer
from .sources.landing import LandedPage, LandingZone, read_page_dir
from .sources.mastr_bulk_xml import iter_bulk_storage_units
from .sources.mastr_json import MastrJsonClient
from .store import Database, to_datetime

SOURCE_SYSTEMS = {"live": "mastr_json", "replay": "mastr_json", "bulk": "mastr_bulk_xml"}


@dataclass
class RunSummary:
    run_id: str
    mode: str
    status: str = "running"
    data_origin: str = "observed"
    pages: int = 0
    records_seen: int = 0
    records_valid: int = 0
    records_rejected: int = 0
    duplicates_exact: int = 0
    duplicates_superseded: int = 0
    stale_skipped: int = 0
    unchanged: int = 0
    out_of_scope_changes: int = 0
    deltas: Counter = field(default_factory=Counter)
    rejections: Counter = field(default_factory=Counter)
    flags: Counter = field(default_factory=Counter)
    since_filter: str | None = None
    watermark_before: str | None = None
    watermark_after: str | None = None
    error: str | None = None

    @property
    def deltas_written(self) -> int:
        return sum(self.deltas.values())

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["deltas"] = dict(self.deltas)
        d["rejections"] = dict(self.rejections)
        d["flags"] = dict(self.flags)
        d["deltas_written"] = self.deltas_written
        return d


def new_run_id(now: datetime) -> str:
    return f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


class Pipeline:
    def __init__(self, cfg: AppConfig, db: Database, session: requests.Session | None = None,
                 sleep=None, clock=lambda: datetime.now(timezone.utc)):
        self.cfg = cfg
        self.db = db
        self.session = session
        self.sleep = sleep
        self.clock = clock

    # ------------------------------------------------------------------ public
    def run(self, mode: str, input_paths: list[str] | None = None, run_id: str | None = None,
            full_refresh: bool = False) -> RunSummary:
        if mode not in SOURCE_SYSTEMS:
            raise ValueError(f"unknown mode {mode}")
        if mode in {"replay", "bulk"} and not input_paths:
            raise ValueError(f"--input is required for mode={mode}")
        now = self.clock()
        run_id = run_id or new_run_id(now)
        source_system = SOURCE_SYSTEMS[mode]
        log = get_logger(__name__, run_id=run_id, source=source_system, mode=mode)
        summary = RunSummary(run_id=run_id, mode=mode, data_origin=self.cfg.data_origin)

        self.db.ensure_schema()
        watermark = self.db.last_watermark(source_system)
        summary.watermark_before = watermark.isoformat() if watermark else None
        since = None
        if mode == "live" and watermark and not full_refresh:
            since = (watermark - timedelta(days=self.cfg.source.overlap_days)).date()
            summary.since_filter = since.isoformat()

        self.db.start_run({
            "run_id": run_id, "started_at": now, "status": "running", "mode": mode,
            "source_system": source_system, "data_origin": self.cfg.data_origin,
            "input_ref": ";".join(input_paths or []) or self.cfg.source.base_url,
            "since_filter": since, "watermark_before": watermark,
            "config_fingerprint": self.cfg.fingerprint(), "code_version": __version__,
        })
        log.info("run started", extra={"since_filter": summary.since_filter,
                                       "watermark_before": summary.watermark_before})
        try:
            pages = self._pages(mode, input_paths, since, run_id, source_system)
            records = self._normalise(pages, source_system, summary, run_id, now, log)
            with self.db.transaction():
                self._stage(records, summary, run_id, now, log, source_system)
                new_wm = self._new_watermark(watermark, records)
                summary.watermark_after = new_wm.isoformat() if new_wm else None
                summary.status = "success"
                self.db.finish_run(run_id, **self._run_fields(summary, new_wm))
        except Exception as exc:  # noqa: BLE001 - record and re-raise
            summary.status = "failed"
            summary.error = f"{type(exc).__name__}: {exc}"
            self.db.finish_run(run_id, status="failed", finished_at=self.clock(), error=summary.error[:2000],
                               pages=summary.pages, records_seen=summary.records_seen)
            log.error("run failed", extra={"error": summary.error})
            raise
        log.info("run finished", extra={"deltas": dict(summary.deltas), "rejected": summary.records_rejected,
                                        "unchanged": summary.unchanged, "stale": summary.stale_skipped})
        return summary

    # ------------------------------------------------------------------ extract
    def _pages(self, mode, input_paths, since, run_id, source_system) -> Iterator[LandedPage]:
        if mode == "replay":
            for p in input_paths:
                yield from read_page_dir(p)
        elif mode == "live":
            landing = LandingZone(self.cfg.landing_dir, source_system, run_id)
            kwargs = {"run_id": run_id}
            if self.sleep is not None:
                kwargs["sleep"] = self.sleep
            client = MastrJsonClient(self.cfg.source, session=self.session, **kwargs)
            yield from client.fetch(since, landing)
        else:  # bulk: land as JSON chunks so the run can be replayed later
            landing = LandingZone(self.cfg.landing_dir, source_system, run_id)
            chunk, page_no = [], 0
            for row in iter_bulk_storage_units(input_paths):
                chunk.append(row)
                if len(chunk) == 5000:
                    page_no += 1
                    yield self._land_chunk(landing, page_no, chunk, input_paths)
                    chunk = []
            if chunk or page_no == 0:
                yield self._land_chunk(landing, page_no + 1, chunk, input_paths)

    @staticmethod
    def _land_chunk(landing: LandingZone, page_no: int, rows: list, inputs) -> LandedPage:
        body = json.dumps({"Data": rows, "Total": None, "Errors": None}, ensure_ascii=False).encode()
        path = landing.write_page(page_no, body, url="file://bulk", params={"inputs": inputs},
                                  http_status=0, record_count=len(rows))
        return LandedPage(str(path), hashlib.sha256(body).hexdigest(), rows, None)

    # ------------------------------------------------------------------ normalise + dedupe
    def _normalise(self, pages: Iterable[LandedPage], source_system, summary: RunSummary, run_id, now, log):
        normalizer = Normalizer(self.cfg, source_system,
                                number_locale="en" if source_system == "mastr_bulk_xml" else None)
        by_id: dict[str, tuple[NormalizedRecord, str]] = {}
        raw_rows: list[tuple] = []
        rejected: list[RejectedRecord] = []
        for page in pages:
            summary.pages += 1
            for raw in page.records:
                summary.records_seen += 1
                result = normalizer.normalize(raw)
                mastr_id = result.mastr_id if isinstance(result, NormalizedRecord) else result.mastr_id_raw
                raw_rows.append((result.payload_sha256, mastr_id, page.file, raw))
                if isinstance(result, RejectedRecord):
                    rejected.append(result)
                    summary.rejections[result.reason_code] += 1
                    log.warning("record rejected", extra={"reason": result.reason_code, "detail": result.detail,
                                                          "mastr_id": result.mastr_id_raw, "file": Path(page.file).name})
                    continue
                prev = by_id.get(result.mastr_id)
                if prev is None:
                    by_id[result.mastr_id] = (result, page.file)
                    continue
                if prev[0].payload_sha256 == result.payload_sha256:
                    summary.duplicates_exact += 1
                    continue
                winner, loser = (result, prev[0]) if _sort_key(result) > _sort_key(prev[0]) else (prev[0], result)
                by_id[result.mastr_id] = (winner, page.file if winner is result else prev[1])
                summary.duplicates_superseded += 1
                rejected.append(RejectedRecord(
                    "DUPLICATE_SUPERSEDED",
                    f"same MaStR id in batch; kept payload {winner.payload_sha256[:12]} (newer/greater)",
                    loser.payload_sha256, loser.mastr_id, None, source_system))
                summary.rejections["DUPLICATE_SUPERSEDED"] += 1
                log.warning("conflicting duplicate in batch", extra={"mastr_id": result.mastr_id})
        records = [rec for rec, _ in sorted(by_id.values(), key=lambda x: x[0].mastr_id)]
        for rec in records:
            summary.flags.update(rec.quality_flags)
        summary.records_valid = len(records)
        summary.records_rejected = sum(1 for r in rejected if r.reason_code != "DUPLICATE_SUPERSEDED")
        self._pending_raw = raw_rows
        self._pending_rejected = rejected
        return records

    # ------------------------------------------------------------------ diff + write
    def _stage(self, records: list[NormalizedRecord], summary: RunSummary, run_id: str, now: datetime, log,
               source_system: str) -> None:
        db = self.db
        for sha, mastr_id, file, raw in self._pending_raw:
            db.insert_raw(sha, source_system, mastr_id, run_id, now, file, raw)
        for rej in self._pending_rejected:
            rid = hashlib.sha256(f"{rej.payload_sha256}|{rej.reason_code}".encode()).hexdigest()
            db.upsert_rejection(rid, rej, run_id, now)

        current = db.load_current()
        for rec in records:
            prev = current.get(rec.mastr_id)
            if prev is None:
                change = "NEW" if rec.in_scope else None
                db.upsert_current(self._current_row(rec, run_id, now, first_seen=run_id), exists=False)
            else:
                prev_updated = to_datetime(prev["source_updated_at"])
                if rec.source_updated_at and prev_updated and rec.source_updated_at < prev_updated:
                    summary.stale_skipped += 1
                    log.info("stale record skipped", extra={"mastr_id": rec.mastr_id,
                                                            "incoming": rec.source_updated_at.isoformat(),
                                                            "stored": prev_updated.isoformat()})
                    continue
                if prev["record_hash"] == rec.record_hash:
                    summary.unchanged += 1
                    db.touch_current(rec.mastr_id, run_id, now, rec.source_updated_at, rec.payload_sha256)
                    continue
                was, now_in = bool(prev["in_scope"]), rec.in_scope
                change = {(True, True): "CHANGED", (True, False): "LEFT_SCOPE",
                          (False, True): "ENTERED_SCOPE", (False, False): None}[(was, now_in)]
                if change is None:
                    summary.out_of_scope_changes += 1
                db.upsert_current(self._current_row(rec, run_id, now, first_seen=prev["first_seen_run_id"]),
                                  exists=True)
            if change:
                previous_state = _state_from_row(prev) if prev else None
                if db.insert_delta(self._delta_row(rec, change, prev, previous_state, run_id, now)):
                    summary.deltas[change] += 1

    def _current_row(self, rec: NormalizedRecord, run_id, now, first_seen) -> dict:
        d = {c: getattr(rec, c) for c in (
            "mastr_id", "record_hash", "in_scope", "scope_reason", "unit_name", "operator_name",
            "storage_mastr_id", "energy_carrier", "storage_technology", "is_battery", "net_power_mw",
            "gross_power_mw", "scope_power_mw", "power_basis", "usable_capacity_mwh", "duration_h",
            "status", "status_code", "status_label_source", "commissioning_date",
            "planned_commissioning_date", "registration_date", "source_updated_at", "postal_code",
            "city", "federal_state", "municipality_key", "latitude", "longitude", "location_quality",
            "source_system", "payload_sha256")}
        d.update(geometry_wkt=rec.geometry_wkt, quality_flags=",".join(rec.quality_flags),
                 data_origin=self.cfg.data_origin, first_seen_run_id=first_seen,
                 last_changed_run_id=run_id, last_seen_run_id=run_id, last_seen_at=now)
        return d

    def _delta_row(self, rec, change, prev, previous_state, run_id, now) -> dict:
        current_state = rec.business_view()
        changed = None
        if previous_state is not None:
            changed = {k: {"old": previous_state.get(k), "new": current_state.get(k)}
                       for k in BUSINESS_FIELDS if previous_state.get(k) != current_state.get(k)}
        prev_hash = prev["record_hash"] if prev else None
        key = f"{rec.mastr_id}|{prev_hash}|{rec.record_hash}|{change}|" \
              f"{rec.source_updated_at.isoformat() if rec.source_updated_at else ''}"
        return {
            "delta_id": hashlib.sha256(key.encode()).hexdigest(),
            "run_id": run_id, "mastr_id": rec.mastr_id, "change_type": change,
            "changed_fields": changed, "previous_state": previous_state, "current_state": current_state,
            "previous_record_hash": prev_hash, "record_hash": rec.record_hash,
            "unit_name": rec.unit_name, "net_power_mw": rec.net_power_mw, "scope_power_mw": rec.scope_power_mw,
            "usable_capacity_mwh": rec.usable_capacity_mwh, "status": rec.status,
            "federal_state": rec.federal_state, "latitude": rec.latitude, "longitude": rec.longitude,
            "source_updated_at": rec.source_updated_at, "detected_at": now,
            "data_origin": self.cfg.data_origin, "source_system": rec.source_system,
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _new_watermark(previous: datetime | None, records: list[NormalizedRecord]) -> datetime | None:
        stamps = [r.source_updated_at for r in records if r.source_updated_at]
        candidates = [d for d in [previous, max(stamps) if stamps else None] if d]
        return max(candidates) if candidates else None

    def _run_fields(self, s: RunSummary, wm) -> dict:
        return {
            "status": s.status, "finished_at": self.clock(), "watermark_after": wm,
            "pages": s.pages, "records_seen": s.records_seen, "records_valid": s.records_valid,
            "records_rejected": s.records_rejected,
            "duplicates_in_batch": s.duplicates_exact + s.duplicates_superseded,
            "stale_skipped": s.stale_skipped, "unchanged": s.unchanged, "deltas_written": s.deltas_written,
        }


def _sort_key(rec: NormalizedRecord):
    ts = rec.source_updated_at or datetime.min.replace(tzinfo=timezone.utc)
    return (ts, rec.payload_sha256)


def _state_from_row(row: dict) -> dict:
    """Rebuild the business view of a stored row so it compares with NormalizedRecord.business_view()."""
    from decimal import Decimal

    from .models import canonical

    out = {}
    for k in BUSINESS_FIELDS:
        v = row.get(k)
        if k in {"net_power_mw", "gross_power_mw", "usable_capacity_mwh", "latitude", "longitude"} and v is not None:
            v = Decimal(str(v))
        if k == "is_battery" and v is not None:
            v = bool(v)
        out[k] = canonical(v)
    for k in ("commissioning_date", "planned_commissioning_date"):
        if out.get(k) is not None:
            out[k] = str(out[k])[:10]
    return out
