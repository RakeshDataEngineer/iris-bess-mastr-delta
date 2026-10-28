from decimal import Decimal

import pytest

from bess_mastr.models import NormalizedRecord, RejectedRecord
from bess_mastr.normalize import Normalizer


def rec(**overrides):
    base = {
        "MaStRNummer": "SEE900000000001",
        "EinheitName": "FIXTURE",
        "EnergietraegerName": "Speicher",
        "TechnologieName": "Lithium-Batterie",
        "Nettonennleistung": 50000,
        "Bruttoleistung": 50000,
        "NutzbareSpeicherkapazitaet": 100000,
        "BetriebsStatusId": 35,
        "BetriebsStatusName": "In Betrieb",
        "DatumLetzteAktualisierung": "/Date(1790812800000)/",
        "Breitengrad": 52.4,
        "Laengengrad": 13.1,
    }
    base.update(overrides)
    return base


@pytest.fixture
def norm(cfg):
    return Normalizer(cfg, "mastr_json")


def test_happy_path(norm):
    r = norm.normalize(rec())
    assert isinstance(r, NormalizedRecord)
    assert r.net_power_mw == Decimal("50") and r.usable_capacity_mwh == Decimal("100")
    assert r.duration_h == Decimal("2.00")
    assert r.status == "operating" and r.in_scope and r.is_battery
    assert r.location_quality == "ok" and r.geometry_wkt == "POINT(13.100000 52.400000)"
    assert r.quality_flags == ()


@pytest.mark.parametrize("kw, in_scope", [(10000, False), (10000.001, True), (10001, True), (9999, False)])
def test_threshold_strictly_greater(norm, kw, in_scope):
    assert norm.normalize(rec(Nettonennleistung=kw)).in_scope is in_scope


@pytest.mark.parametrize("overrides, code", [
    ({"MaStRNummer": None}, "MISSING_MASTR_ID"),
    ({"MaStRNummer": "   "}, "MISSING_MASTR_ID"),
    ({"MaStRNummer": "SEE12"}, "INVALID_MASTR_ID"),
    ({"Nettonennleistung": "abc"}, "MALFORMED_POWER"),
    ({"Nettonennleistung": None, "Bruttoleistung": None}, "MISSING_POWER"),
    ({"Nettonennleistung": -5}, "NEGATIVE_POWER"),
    ({"Nettonennleistung": 5_000_000_000}, "IMPLAUSIBLE_POWER"),
])
def test_rejections(norm, overrides, code):
    r = norm.normalize(rec(**overrides))
    assert isinstance(r, RejectedRecord) and r.reason_code == code


def test_not_a_dict_rejected(norm):
    assert norm.normalize(["x"]).reason_code == "NOT_A_RECORD"


def test_lowercase_id_is_normalised(norm):
    assert norm.normalize(rec(MaStRNummer="see900000000001")).mastr_id == "SEE900000000001"


def test_gross_fallback(norm):
    r = norm.normalize(rec(Nettonennleistung=None, Bruttoleistung=15000))
    assert r.power_basis == "gross" and r.in_scope and "POWER_FROM_GROSS" in r.quality_flags


def test_non_battery_storage_out_of_scope(norm):
    r = norm.normalize(rec(TechnologieName="Pumpspeicher", Nettonennleistung=200000))
    assert not r.in_scope and r.scope_reason == "not_battery_technology"


def test_unknown_technology_included_but_flagged(norm):
    r = norm.normalize(rec(TechnologieName=None))
    assert r.in_scope and r.is_battery is None and "TECHNOLOGY_UNKNOWN" in r.quality_flags


def test_unknown_technology_can_be_excluded(cfg):
    from dataclasses import replace
    strict = replace(cfg, scope=replace(cfg.scope, include_unknown_technology=False))
    r = Normalizer(strict, "mastr_json").normalize(rec(TechnologieName=None))
    assert not r.in_scope


def test_non_storage_carrier_out_of_scope(norm):
    r = norm.normalize(rec(EnergietraegerName="Wind"))
    assert not r.in_scope and "NOT_STORAGE_CARRIER" in r.quality_flags


@pytest.mark.parametrize("lat, lon, quality, flag", [
    (None, None, "missing", "LOCATION_MISSING"),
    (0.0, 0.0, "out_of_bounds", "LOCATION_OUT_OF_BOUNDS"),
    ("x", 1, "missing", "LOCATION_MALFORMED"),
])
def test_location_flags_do_not_reject(norm, lat, lon, quality, flag):
    r = norm.normalize(rec(Breitengrad=lat, Laengengrad=lon))
    assert isinstance(r, NormalizedRecord) and r.location_quality == quality and flag in r.quality_flags
    assert r.geometry_wkt is None


def test_capacity_issues_are_flags(norm):
    assert "CAPACITY_MISSING" in norm.normalize(rec(NutzbareSpeicherkapazitaet=None)).quality_flags
    assert "CAPACITY_MALFORMED" in norm.normalize(rec(NutzbareSpeicherkapazitaet="lots")).quality_flags
    assert "DURATION_IMPLAUSIBLE" in norm.normalize(rec(NutzbareSpeicherkapazitaet=5_000_000)).quality_flags


def test_status_from_label_when_code_missing(norm):
    r = norm.normalize(rec(BetriebsStatusId=None, BetriebsStatusName="Endgültig stillgelegt"))
    assert r.status == "permanently_shut_down"
    assert norm.normalize(rec(BetriebsStatusId=99, BetriebsStatusName=None)).status == "unknown"


def test_bad_timestamp_is_flag_not_rejection(norm):
    r = norm.normalize(rec(DatumLetzteAktualisierung="garbage"))
    assert r.source_updated_at is None and "UPDATED_AT_UNPARSEABLE" in r.quality_flags


def test_hash_ignores_bookkeeping_but_tracks_business_fields(norm):
    a = norm.normalize(rec())
    bumped = norm.normalize(rec(DatumLetzteAktualisierung="/Date(1800000000000)/"))
    changed = norm.normalize(rec(BetriebsStatusId=37, BetriebsStatusName="Vorübergehend stillgelegt"))
    assert a.record_hash == bumped.record_hash
    assert a.payload_sha256 != bumped.payload_sha256
    assert a.record_hash != changed.record_hash


def test_equal_values_in_different_units_hash_equal(norm):
    a = norm.normalize(rec(Nettonennleistung=50000))
    b = norm.normalize(rec(Nettonennleistung="50.000,000"))
    assert a.net_power_mw == b.net_power_mw and a.record_hash == b.record_hash
