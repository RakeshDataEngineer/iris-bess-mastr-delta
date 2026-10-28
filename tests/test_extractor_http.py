"""Live-mode extractor against a mocked MaStR endpoint (no network)."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from requests.adapters import BaseAdapter

from bess_mastr.sources.mastr_json import SourceError, build_filter

from .conftest import SNAP1, deltas


class FakeMastr(BaseAdapter):
    """Serves fixture records with real paging semantics; can inject failures."""

    def __init__(self, records, failures=None, body_override=None):
        super().__init__()
        self.records = records
        self.failures = list(failures or [])  # status codes to return before succeeding
        self.body_override = body_override
        self.calls = []

    def send(self, request, **kwargs):
        q = {k: v[0] for k, v in parse_qs(urlparse(request.url).query, keep_blank_values=True).items()}
        self.calls.append(q)
        resp = requests.Response()
        resp.url = request.url
        resp.request = request
        if self.failures:
            resp.status_code = self.failures.pop(0)
            resp._content = b"busy"
            return resp
        resp.status_code = 200
        if self.body_override is not None:
            resp._content = self.body_override
            return resp
        page, size = int(q["page"]), int(q["pageSize"])
        chunk = self.records[(page - 1) * size: page * size]
        resp._content = json.dumps({"Data": chunk, "Total": len(self.records), "Errors": None}).encode()
        return resp

    def close(self):
        pass


def fixture_records():
    out = []
    for f in sorted(Path(SNAP1).glob("page_*.json")):
        out.extend(json.loads(f.read_text())["Data"])
    return out


def session_with(adapter):
    s = requests.Session()
    s.mount("https://", adapter)
    return s


@pytest.fixture
def live(cfg, db):
    from datetime import datetime, timezone

    from bess_mastr.pipeline import Pipeline

    def make(adapter):
        return Pipeline(cfg, db, session=session_with(adapter), sleep=lambda s: None,
                        clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc))
    return make


def test_filter_string(cfg):
    from datetime import date
    f = build_filter(cfg.source, date(2026, 9, 30))
    assert f == ("Energieträger~eq~'2496'~and~Nettonennleistung der Einheit~gt~'1000'"
                 "~and~Datum der letzten Aktualisierung~gt~'30.09.2026'")
    assert "Datum" not in build_filter(cfg.source, None)


def test_live_paginates_lands_and_stages(live, db, cfg):
    adapter = FakeMastr(fixture_records())
    s = live(adapter).run("live", run_id="L1")
    assert s.pages == 5 and s.records_seen == 15  # page_size=3 in test config
    assert [c["page"] for c in adapter.calls] == ["1", "2", "3", "4", "5"]
    assert all(c["sort"] == "MaStRNummer-asc" for c in adapter.calls)
    assert "~and~Datum" not in adapter.calls[0]["filter"]  # first run: no watermark yet
    landed = Path(cfg.landing_dir) / "mastr_json" / "L1"
    assert len(list(landed.glob("page_*.json"))) == 5
    manifest = [json.loads(line) for line in (landed / "manifest.jsonl").read_text().splitlines()]
    assert all(len(m["sha256"]) == 64 for m in manifest)
    assert len(deltas(db, "L1")) == 7


def test_second_live_run_is_incremental_with_overlap(live, db):
    live(FakeMastr(fixture_records())).run("live", run_id="L1")
    adapter = FakeMastr(fixture_records())
    s = live(adapter).run("live", run_id="L2")
    # max DatumLetzteAktualisierung in snapshot 1 is 05.09.2026, overlap 2 days
    assert adapter.calls[0]["filter"].endswith("~gt~'03.09.2026'")
    assert s.deltas_written == 0


def test_replay_of_landed_live_run_matches(live, db, cfg, pipeline):
    live(FakeMastr(fixture_records())).run("live", run_id="L1")
    landed = str(Path(cfg.landing_dir) / "mastr_json" / "L1")
    assert pipeline.run("replay", [landed], run_id="R1").deltas_written == 0


def test_retries_transient_errors(live, db):
    adapter = FakeMastr(fixture_records(), failures=[503, 429])
    s = live(adapter).run("live", run_id="L1")
    assert s.status == "success" and len(deltas(db)) == 7


def test_gives_up_and_marks_run_failed(live, db):
    adapter = FakeMastr(fixture_records(), failures=[503, 503, 503, 503])
    with pytest.raises(SourceError):
        live(adapter).run("live", run_id="L1")
    run = db.query(f"SELECT status, error FROM {db.t('pipeline_runs')} WHERE run_id = 'L1'")[0]
    assert run["status"] == "failed" and "503" in run["error"]
    assert deltas(db) == [] and db.last_watermark("mastr_json") is None


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b'{"Data": null}', b'{"Errors": ["boom"]}'])
def test_malformed_response_fails_safely(live, db, cfg, body):
    with pytest.raises(SourceError):
        live(FakeMastr([], body_override=body)).run("live", run_id="bad")
    assert deltas(db) == []
    # the offending body is kept for forensics
    assert (Path(cfg.landing_dir) / "mastr_json" / "bad" / "page_0001.json").read_bytes() == body


def test_non_retryable_http_error(live):
    with pytest.raises(SourceError, match="HTTP 404"):
        live(FakeMastr([], failures=[404])).run("live", run_id="x")


def test_failed_run_does_not_leave_partial_deltas(live, db, monkeypatch):
    """A crash during staging must roll back the whole batch."""
    from bess_mastr import store

    original = store.Database.insert_delta
    calls = {"n": 0}

    def flaky(self, row):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        return original(self, row)

    monkeypatch.setattr(store.Database, "insert_delta", flaky)
    with pytest.raises(RuntimeError):
        live(FakeMastr(fixture_records())).run("live", run_id="crash")
    assert deltas(db) == []
    assert db.query(f"SELECT COUNT(*) AS n FROM {db.t('bess_projects_current')}")[0]["n"] == 0
    monkeypatch.setattr(store.Database, "insert_delta", original)
    assert live(FakeMastr(fixture_records())).run("live", run_id="retry").deltas_written == 7
