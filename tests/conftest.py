from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bess_mastr.config import AppConfig, DbConfig, SourceConfig
from bess_mastr.pipeline import Pipeline
from bess_mastr.store import Database

FIXTURES = Path(__file__).parent / "fixtures"
SNAP1 = str(FIXTURES / "mastr_json" / "snapshot_1")
SNAP2 = str(FIXTURES / "mastr_json" / "snapshot_2")
BULK = str(FIXTURES / "mastr_bulk")


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    return AppConfig(
        source=replace(SourceConfig(), base_url="https://mastr.test/EinheitJson", page_size=3,
                       request_interval_s=0, backoff_s=0, max_retries=2),
        db=DbConfig(url=f"sqlite:///{tmp_path / 'staging.sqlite'}"),
        landing_dir=str(tmp_path / "landing"),
        reports_dir=str(tmp_path / "reports"),
        data_origin="fixture",
    )


@pytest.fixture
def db(cfg):
    database = Database(cfg.db)
    yield database
    database.close()


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def pipeline(cfg, db):
    return Pipeline(cfg, db, sleep=lambda s: None, clock=Clock())


def deltas(db, run_id=None):
    sql = f"SELECT * FROM {db.t('bess_pipeline_deltas')}"
    if run_id:
        return db.query(sql + " WHERE run_id = ? ORDER BY mastr_id", (run_id,))
    return db.query(sql + " ORDER BY run_id, mastr_id")


def by_id(rows):
    return {r["mastr_id"]: r for r in rows}
