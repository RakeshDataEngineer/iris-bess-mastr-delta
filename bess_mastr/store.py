"""Staging persistence (PostgreSQL in Docker, SQLite for tests/local runs).

Plain DB-API + portable SQL keeps the dependency surface small and the SQL
easy to review. Every table lives in the ``iris_staging`` schema; the config
loader refuses ``iris_core``.

Tables
------
pipeline_runs           one row per run: mode, watermark, counts, status
bess_raw_records        every distinct raw payload ever received (sha256 key)
bess_rejected_records   payloads that could not be normalised + reason
bess_projects_current   latest normalised state per MaStR unit (all candidates)
bess_pipeline_deltas    append-only change history (NEW / CHANGED /
                        ENTERED_SCOPE / LEFT_SCOPE) - the task's destination
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .config import DbConfig, FORBIDDEN_SCHEMAS
from .models import canonical

sqlite3.register_adapter(Decimal, lambda d: format(d, "f"))
sqlite3.register_adapter(datetime, lambda d: d.isoformat())
sqlite3.register_adapter(date, lambda d: d.isoformat())

CURRENT_COLUMNS = (
    "mastr_id", "record_hash", "in_scope", "scope_reason", "unit_name", "operator_name",
    "storage_mastr_id", "energy_carrier", "storage_technology", "is_battery",
    "net_power_mw", "gross_power_mw", "scope_power_mw", "power_basis",
    "usable_capacity_mwh", "duration_h", "status", "status_code", "status_label_source",
    "commissioning_date", "planned_commissioning_date", "registration_date",
    "source_updated_at", "postal_code", "city", "federal_state", "municipality_key",
    "latitude", "longitude", "location_quality", "geometry_wkt", "quality_flags",
    "source_system", "payload_sha256", "data_origin",
    "first_seen_run_id", "last_changed_run_id", "last_seen_run_id", "last_seen_at",
)


class Database:
    def __init__(self, cfg: DbConfig):
        if cfg.schema.lower() in FORBIDDEN_SCHEMAS:
            raise ValueError(f"refusing schema {cfg.schema}")
        self.cfg = cfg
        self.schema = cfg.schema
        url = cfg.url
        if url.startswith("sqlite:///"):
            self.dialect = "sqlite"
            path = url[len("sqlite:///"):]
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, isolation_level=None, detect_types=0)
            # emulate a schema: the main DB is unused, everything lives in
            # an attached database named like the schema
            attach = ":memory:" if path == ":memory:" else str(Path(path).with_suffix("")) + f".{self.schema}.sqlite"
            self.conn.execute(f"ATTACH DATABASE ? AS {self.schema}", (attach,))
            self.conn.execute("PRAGMA foreign_keys=ON")
        elif url.startswith(("postgresql://", "postgres://")):
            import psycopg  # imported lazily so tests do not need it

            self.dialect = "postgres"
            self.conn = psycopg.connect(url, autocommit=True)
        else:
            raise ValueError(f"unsupported database url: {url.split('://')[0]}://...")

    # ------------------------------------------------------------------ utils
    def t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def execute(self, sql: str, params: Iterable[Any] = ()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        cur = self.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @contextmanager
    def transaction(self):
        if self.dialect == "sqlite":
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")
        else:
            with self.conn.transaction():
                yield

    def json_param(self) -> str:
        return "CAST(? AS JSONB)" if self.dialect == "postgres" else "?"

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ DDL
    def ensure_schema(self) -> None:
        pg = self.dialect == "postgres"
        ts = "TIMESTAMPTZ" if pg else "TEXT"
        dt = "DATE" if pg else "TEXT"
        js = "JSONB" if pg else "TEXT"
        num = "NUMERIC(18,6)"
        geo = "NUMERIC(10,6)"
        if pg:
            self.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
        stmts = [
            f"""CREATE TABLE IF NOT EXISTS {self.t('pipeline_runs')} (
                run_id TEXT PRIMARY KEY,
                started_at {ts} NOT NULL,
                finished_at {ts},
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                source_system TEXT NOT NULL,
                data_origin TEXT NOT NULL,
                input_ref TEXT,
                since_filter {dt},
                watermark_before {ts},
                watermark_after {ts},
                pages INTEGER DEFAULT 0,
                records_seen INTEGER DEFAULT 0,
                records_valid INTEGER DEFAULT 0,
                records_rejected INTEGER DEFAULT 0,
                duplicates_in_batch INTEGER DEFAULT 0,
                stale_skipped INTEGER DEFAULT 0,
                unchanged INTEGER DEFAULT 0,
                deltas_written INTEGER DEFAULT 0,
                config_fingerprint TEXT,
                code_version TEXT,
                error TEXT
            )""",
            f"""CREATE TABLE IF NOT EXISTS {self.t('bess_raw_records')} (
                payload_sha256 TEXT PRIMARY KEY,
                source_system TEXT NOT NULL,
                mastr_id TEXT,
                first_seen_run_id TEXT NOT NULL,
                first_seen_at {ts} NOT NULL,
                landing_file TEXT,
                payload {js} NOT NULL
            )""",
            f"""CREATE TABLE IF NOT EXISTS {self.t('bess_rejected_records')} (
                rejection_id TEXT PRIMARY KEY,
                payload_sha256 TEXT NOT NULL,
                source_system TEXT NOT NULL,
                mastr_id_raw TEXT,
                reason_code TEXT NOT NULL,
                detail TEXT,
                first_seen_run_id TEXT NOT NULL,
                last_seen_run_id TEXT NOT NULL,
                first_seen_at {ts} NOT NULL
            )""",
            f"""CREATE TABLE IF NOT EXISTS {self.t('bess_projects_current')} (
                mastr_id TEXT PRIMARY KEY,
                record_hash TEXT NOT NULL,
                in_scope BOOLEAN NOT NULL,
                scope_reason TEXT,
                unit_name TEXT, operator_name TEXT, storage_mastr_id TEXT,
                energy_carrier TEXT, storage_technology TEXT, is_battery BOOLEAN,
                net_power_mw {num}, gross_power_mw {num}, scope_power_mw {num} NOT NULL,
                power_basis TEXT NOT NULL,
                usable_capacity_mwh {num}, duration_h {num},
                status TEXT NOT NULL, status_code TEXT, status_label_source TEXT,
                commissioning_date {dt}, planned_commissioning_date {dt}, registration_date {dt},
                source_updated_at {ts},
                postal_code TEXT, city TEXT, federal_state TEXT, municipality_key TEXT,
                latitude {geo}, longitude {geo}, location_quality TEXT NOT NULL,
                geometry_wkt TEXT,
                quality_flags TEXT,
                source_system TEXT NOT NULL, payload_sha256 TEXT NOT NULL, data_origin TEXT NOT NULL,
                first_seen_run_id TEXT NOT NULL, last_changed_run_id TEXT NOT NULL,
                last_seen_run_id TEXT NOT NULL, last_seen_at {ts} NOT NULL
            )""",
            f"""CREATE TABLE IF NOT EXISTS {self.t(self.cfg.deltas_table)} (
                delta_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                mastr_id TEXT NOT NULL,
                change_type TEXT NOT NULL,
                changed_fields {js},
                previous_state {js},
                current_state {js} NOT NULL,
                previous_record_hash TEXT,
                record_hash TEXT NOT NULL,
                unit_name TEXT,
                net_power_mw {num},
                scope_power_mw {num},
                usable_capacity_mwh {num},
                status TEXT,
                federal_state TEXT,
                latitude {geo},
                longitude {geo},
                source_updated_at {ts},
                detected_at {ts} NOT NULL,
                data_origin TEXT NOT NULL,
                source_system TEXT NOT NULL,
                CHECK (change_type IN ('NEW','CHANGED','ENTERED_SCOPE','LEFT_SCOPE'))
            )""",
        ]
        for s in stmts:
            self.execute(s)
        idx = [
            ("ix_deltas_mastr", self.cfg.deltas_table, "mastr_id"),
            ("ix_deltas_run", self.cfg.deltas_table, "run_id"),
            ("ix_current_scope", "bess_projects_current", "in_scope"),
            ("ix_rejected_reason", "bess_rejected_records", "reason_code"),
        ]
        for name, table, col in idx:
            if pg:
                self.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {self.t(table)} ({col})")
            else:
                self.execute(f"CREATE INDEX IF NOT EXISTS {self.schema}.{name} ON {table} ({col})")
        view = f"""SELECT mastr_id, unit_name, operator_name, net_power_mw, scope_power_mw, power_basis,
                    usable_capacity_mwh, duration_h, status, commissioning_date, planned_commissioning_date,
                    federal_state, city, postal_code, latitude, longitude, geometry_wkt, quality_flags,
                    source_updated_at, data_origin, last_changed_run_id
                   FROM {self.t('bess_projects_current')} WHERE in_scope"""
        if pg:
            self.execute(f"CREATE OR REPLACE VIEW {self.t('v_bess_large_battery_current')} AS {view}")
        else:
            self.execute(f"CREATE VIEW IF NOT EXISTS {self.schema}.v_bess_large_battery_current AS "
                         + view.replace(f"{self.schema}.", ""))

    # ------------------------------------------------------------------ runs
    def last_watermark(self, source_system: str) -> datetime | None:
        # MAX over successful runs: monotonic even if runs overlap or a replay
        # of older data finishes last. (All values are UTC ISO-8601.)
        rows = self.query(
            f"SELECT MAX(watermark_after) AS wm FROM {self.t('pipeline_runs')} "
            "WHERE status = 'success' AND source_system = ?",
            (source_system,),
        )
        return to_datetime(rows[0]["wm"]) if rows and rows[0]["wm"] else None

    def start_run(self, row: dict) -> None:
        cols = ", ".join(row)
        self.execute(f"INSERT INTO {self.t('pipeline_runs')} ({cols}) VALUES ({', '.join('?' * len(row))})",
                     row.values())

    def finish_run(self, run_id: str, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE {self.t('pipeline_runs')} SET {sets} WHERE run_id = ?", [*fields.values(), run_id])

    # ------------------------------------------------------------------ raw / rejects
    def insert_raw(self, sha: str, source: str, mastr_id: str | None, run_id: str, now: datetime,
                   landing_file: str | None, payload: Any) -> bool:
        exists = self.query(f"SELECT 1 FROM {self.t('bess_raw_records')} WHERE payload_sha256 = ?", (sha,))
        if exists:
            return False
        self.execute(
            f"INSERT INTO {self.t('bess_raw_records')} (payload_sha256, source_system, mastr_id, "
            f"first_seen_run_id, first_seen_at, landing_file, payload) VALUES (?, ?, ?, ?, ?, ?, {self.json_param()})",
            (sha, source, mastr_id, run_id, now, landing_file, dumps(payload)),
        )
        return True

    def upsert_rejection(self, rejection_id: str, rej, run_id: str, now: datetime) -> bool:
        exists = self.query(f"SELECT 1 FROM {self.t('bess_rejected_records')} WHERE rejection_id = ?", (rejection_id,))
        if exists:
            self.execute(f"UPDATE {self.t('bess_rejected_records')} SET last_seen_run_id = ? WHERE rejection_id = ?",
                         (run_id, rejection_id))
            return False
        self.execute(
            f"INSERT INTO {self.t('bess_rejected_records')} (rejection_id, payload_sha256, source_system, "
            "mastr_id_raw, reason_code, detail, first_seen_run_id, last_seen_run_id, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rejection_id, rej.payload_sha256, rej.source_system, rej.mastr_id_raw, rej.reason_code,
             rej.detail, run_id, run_id, now),
        )
        return True

    # ------------------------------------------------------------------ current state
    def load_current(self) -> dict[str, dict]:
        rows = self.query(f"SELECT * FROM {self.t('bess_projects_current')}")
        return {r["mastr_id"]: r for r in rows}

    def upsert_current(self, row: dict, exists: bool) -> None:
        if exists:
            cols = [c for c in CURRENT_COLUMNS if c != "mastr_id"]
            sets = ", ".join(f"{c} = ?" for c in cols)
            self.execute(f"UPDATE {self.t('bess_projects_current')} SET {sets} WHERE mastr_id = ?",
                         [row[c] for c in cols] + [row["mastr_id"]])
        else:
            self.execute(
                f"INSERT INTO {self.t('bess_projects_current')} ({', '.join(CURRENT_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(CURRENT_COLUMNS))})",
                [row[c] for c in CURRENT_COLUMNS],
            )

    def touch_current(self, mastr_id: str, run_id: str, now: datetime, source_updated_at, payload_sha: str) -> None:
        self.execute(
            f"UPDATE {self.t('bess_projects_current')} SET last_seen_run_id = ?, last_seen_at = ?, "
            "source_updated_at = COALESCE(?, source_updated_at), payload_sha256 = ? WHERE mastr_id = ?",
            (run_id, now, source_updated_at, payload_sha, mastr_id),
        )

    # ------------------------------------------------------------------ deltas
    def insert_delta(self, row: dict) -> bool:
        table = self.t(self.cfg.deltas_table)
        if self.query(f"SELECT 1 FROM {table} WHERE delta_id = ?", (row["delta_id"],)):
            return False
        cols = list(row)
        json_cols = {"changed_fields", "previous_state", "current_state"}
        placeholders = ", ".join(self.json_param() if c in json_cols else "?" for c in cols)
        self.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                     [dumps(row[c]) if c in json_cols and row[c] is not None else row[c] for c in cols])
        return True


def dumps(obj: Any) -> str:
    return json.dumps(canonical(obj), ensure_ascii=False, sort_keys=True, default=str)


def to_datetime(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value if (value is None or value.tzinfo) else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
