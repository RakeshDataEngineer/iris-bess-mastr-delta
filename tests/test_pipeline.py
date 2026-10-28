"""End-to-end staging behaviour on the replayable fixture snapshots."""

import json
from decimal import Decimal

import pytest

from bess_mastr.config import ConfigError, DbConfig, load_config, validate_config
from bess_mastr.report import build_report, export_deltas_csv
from bess_mastr.store import Database

from .conftest import BULK, SNAP1, SNAP2, by_id, deltas


def current(db):
    return by_id(db.query(f"SELECT * FROM {db.t('bess_projects_current')}"))


def test_first_load_detects_new_large_batteries(pipeline, db):
    s = pipeline.run("replay", [SNAP1], run_id="r1")
    assert s.status == "success"
    d = by_id(deltas(db, "r1"))
    assert set(d) == {f"SEE9000000000{n}" for n in ("01", "03", "05", "10", "11", "13", "14")}
    assert {r["change_type"] for r in d.values()} == {"NEW"}
    # exact 10 MW, 9.999 MW and pumped hydro are staged but NOT emitted
    cur = current(db)
    assert not cur["SEE900000000002"]["in_scope"]
    assert not cur["SEE900000000012"]["in_scope"]
    assert not cur["SEE900000000004"]["in_scope"]
    assert Decimal(str(cur["SEE900000000005"]["net_power_mw"])) == Decimal("10.0005")
    assert s.rejections == {"MISSING_MASTR_ID": 1, "INVALID_MASTR_ID": 1, "MALFORMED_POWER": 1,
                            "IMPLAUSIBLE_POWER": 1}
    assert s.duplicates_exact == 1
    assert s.records_seen == 15 and s.records_valid == 10


def test_second_load_classifies_changes(pipeline, db):
    pipeline.run("replay", [SNAP1], run_id="r1")
    s = pipeline.run("replay", [SNAP2], run_id="r2")
    d = by_id(deltas(db, "r2"))
    assert {k: v["change_type"] for k, v in d.items()} == {
        "SEE900000000001": "CHANGED",        # capacity 100 -> 120 MWh
        "SEE900000000002": "ENTERED_SCOPE",  # 10 -> 25 MW
        "SEE900000000003": "CHANGED",        # planned -> operating
        "SEE900000000011": "LEFT_SCOPE",     # 30 -> 8 MW
        "SEE900000000016": "NEW",
    }
    alpha = json.loads(d["SEE900000000001"]["changed_fields"])
    assert alpha == {"usable_capacity_mwh": {"old": "100", "new": "120"}}
    gamma = json.loads(d["SEE900000000003"]["changed_fields"])
    assert gamma["status"] == {"old": "planned", "new": "operating"}
    assert s.stale_skipped == 1          # older Mike version ignored
    assert s.unchanged == 1              # November: only timestamp bumped
    assert s.duplicates_superseded == 1  # Papa twice, newest wins
    assert Decimal(str(d["SEE900000000016"]["net_power_mw"])) == Decimal("45")
    assert Decimal(str(current(db)["SEE900000000013"]["net_power_mw"])) == Decimal("20")


@pytest.mark.parametrize("sequence", [[SNAP1, SNAP1], [SNAP1, SNAP2, SNAP2], [SNAP1, SNAP2, SNAP1, SNAP2]])
def test_repeated_ingestion_is_idempotent(pipeline, db, sequence):
    pipeline.run("replay", [SNAP1], run_id="base")
    pipeline.run("replay", [SNAP2], run_id="base2") if SNAP2 in sequence else None
    before_deltas = len(deltas(db))
    before_current = {k: (v["record_hash"], v["in_scope"]) for k, v in current(db).items()}
    before_raw = db.query(f"SELECT COUNT(*) AS n FROM {db.t('bess_raw_records')}")[0]["n"]
    before_rej = db.query(f"SELECT COUNT(*) AS n FROM {db.t('bess_rejected_records')}")[0]["n"]
    for i, snap in enumerate(sequence):
        s = pipeline.run("replay", [snap], run_id=f"again{i}")
        assert s.deltas_written == 0, f"replay {i} produced deltas {s.deltas}"
    assert len(deltas(db)) == before_deltas
    assert {k: (v["record_hash"], v["in_scope"]) for k, v in current(db).items()} == before_current
    assert db.query(f"SELECT COUNT(*) AS n FROM {db.t('bess_raw_records')}")[0]["n"] == before_raw
    assert db.query(f"SELECT COUNT(*) AS n FROM {db.t('bess_rejected_records')}")[0]["n"] == before_rej


def test_no_data_loss_every_payload_landed(pipeline, db):
    pipeline.run("replay", [SNAP1], run_id="r1")
    pipeline.run("replay", [SNAP2], run_id="r2")
    raw = db.query(f"SELECT COUNT(*) AS n FROM {db.t('bess_raw_records')}")[0]["n"]
    # 15 records in snap1 with one exact duplicate -> 14 distinct; snap2 has 8 distinct, of
    # which none equals a snap1 payload (all carry new timestamps)
    assert raw == 14 + 8
    rejected = db.query(f"SELECT reason_code FROM {db.t('bess_rejected_records')}")
    assert sorted(r["reason_code"] for r in rejected) == sorted(
        ["MISSING_MASTR_ID", "INVALID_MASTR_ID", "MALFORMED_POWER", "IMPLAUSIBLE_POWER", "DUPLICATE_SUPERSEDED"])


def test_watermark_recorded_and_monotonic(pipeline, db):
    pipeline.run("replay", [SNAP1], run_id="r1")
    pipeline.run("replay", [SNAP2], run_id="r2")
    wm2 = db.last_watermark("mastr_json")
    pipeline.run("replay", [SNAP1], run_id="r3")  # older data must not move the watermark back
    assert db.last_watermark("mastr_json") == wm2
    assert wm2.isoformat().startswith("2026-10-07")


def test_every_row_is_labelled_with_origin(pipeline, db):
    pipeline.run("replay", [SNAP1], run_id="r1")
    assert {r["data_origin"] for r in deltas(db)} == {"fixture"}
    runs = db.query(f"SELECT data_origin, status, config_fingerprint FROM {db.t('pipeline_runs')}")
    assert runs[0]["data_origin"] == "fixture" and runs[0]["status"] == "success" and runs[0]["config_fingerprint"]


def test_report_acceptance_checks(pipeline, db, cfg):
    pipeline.run("replay", [SNAP1], run_id="r1")
    s = pipeline.run("replay", [SNAP2], run_id="r2")
    rep = build_report(db, s, cfg.scope.threshold_mw)
    assert rep["acceptance_checks"] == {"duplicate_delta_transitions": 0, "in_scope_rows_at_or_below_threshold": 0}
    assert rep["coverage"]["in_scope_projects"] == 8


def test_export_csv(pipeline, db, tmp_path):
    pipeline.run("replay", [SNAP1], run_id="r1")
    n = export_deltas_csv(db, tmp_path / "d.csv")
    assert n == 7 and (tmp_path / "d.csv").read_text().count("\n") == 8


def test_bulk_xml_mode(pipeline, db):
    s = pipeline.run("bulk", [BULK], run_id="b1")
    d = by_id(deltas(db, "b1"))
    assert list(d) == ["SEE900000000101"]  # 20 MW battery; the 10 kW ("10.000") home battery is out
    row = d["SEE900000000101"]
    assert Decimal(str(row["usable_capacity_mwh"])) == Decimal("40")  # joined from AnlagenStromSpeicher
    home = current(db)["SEE900000000102"]
    assert Decimal(str(home["net_power_mw"])) == Decimal("0.01") and not home["in_scope"]
    assert s.status == "success"


def test_bulk_xml_utf16(pipeline, db, tmp_path):
    from pathlib import Path
    for f in Path(BULK).glob("*.xml"):
        text = f.read_text(encoding="utf-8").replace('encoding="utf-8"', 'encoding="utf-16"')
        (tmp_path / f.name).write_text(text, encoding="utf-16")
    pipeline.run("bulk", [str(tmp_path)], run_id="b16")
    assert len(deltas(db, "b16")) == 1


def test_refuses_iris_core():
    with pytest.raises(ValueError):
        Database(DbConfig(url="sqlite:///:memory:", schema="iris_core"))


def test_config_guards(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("db:\n  schema: iris_core\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("source:\n  candidate_floor_kw: 20000\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("source:\n  typo_key: 1\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_example_config_is_valid():
    from pathlib import Path
    cfg = load_config(Path(__file__).parents[1] / "config" / "config.example.yaml")
    validate_config(cfg)
    assert cfg.db.schema == "iris_staging"
