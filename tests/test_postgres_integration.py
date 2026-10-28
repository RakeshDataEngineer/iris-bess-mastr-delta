"""Same acceptance scenario against real PostgreSQL.

Skipped unless IRIS_TEST_PG_URL is set. `docker compose run --rm tests` sets it
to the compose Postgres, so the suite exercises both backends there.
Each test uses a throw-away schema (iris_staging_test_<random>) that is dropped afterwards.
"""

import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bess_mastr.config import DbConfig
from bess_mastr.pipeline import Pipeline
from bess_mastr.report import build_report
from bess_mastr.store import Database

from .conftest import BULK, SNAP1, SNAP2

PG_URL = os.getenv("IRIS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="IRIS_TEST_PG_URL not set")


@pytest.fixture
def pg(cfg):
    schema = f"iris_staging_test_{uuid.uuid4().hex[:8]}"
    pcfg = replace(cfg, db=DbConfig(url=PG_URL, schema=schema))
    db = Database(pcfg.db)
    yield pcfg, db
    db.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    db.close()


def test_postgres_end_to_end(pg):
    cfg, db = pg
    p = Pipeline(cfg, db, clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert p.run("replay", [SNAP1], run_id="r1").deltas == {"NEW": 7}
    s2 = p.run("replay", [SNAP2], run_id="r2")
    assert dict(s2.deltas) == {"CHANGED": 2, "ENTERED_SCOPE": 1, "LEFT_SCOPE": 1, "NEW": 1}
    assert p.run("replay", [SNAP2], run_id="r3").deltas_written == 0
    assert p.run("replay", [SNAP1], run_id="r4").deltas_written == 0
    assert p.run("bulk", [BULK], run_id="b1").deltas == {"NEW": 1}
    rep = build_report(db, s2, cfg.scope.threshold_mw)
    assert rep["acceptance_checks"] == {"duplicate_delta_transitions": 0, "in_scope_rows_at_or_below_threshold": 0}
    row = db.query(f"SELECT net_power_mw FROM {db.t('bess_projects_current')} WHERE mastr_id = 'SEE900000000005'")[0]
    assert Decimal(row["net_power_mw"]) == Decimal("10.0005")
    assert db.last_watermark("mastr_json").isoformat().startswith("2026-10-07")
