"""Records shaped exactly like the live MaStR public table (field names verified
2026-10-28). Values are synthetic; only the field names/types mirror the source."""

from decimal import Decimal

import pytest

from bess_mastr.normalize import Normalizer


def live_like(**overrides):
    base = {
        "MaStRNummer": "SEE900000000201",
        "EinheitName": "FIXTURE live-shaped unit",
        "AnlagenbetreiberName": "FIXTURE GmbH",
        "BetriebsStatusId": 31,
        "BetriebsStatusName": "In Planung",
        "Breitengrad": 51.45,
        "Laengengrad": 12.38,
        "Bundesland": "Sachsen",
        "Gemeindeschluessel": "14730250",
        "Plz": "04519",
        "Ort": "Fixtureort",
        "DatumLetzteAktualisierung": "/Date(1787145490624)/",
        "EinheitRegistrierungsdatum": "/Date(1787097600000)/",
        "GeplantesInbetriebsnahmeDatum": "/Date(1842566400000)/",
        "InbetriebnahmeDatum": None,
        "EnergietraegerId": 2496,
        "EnergietraegerName": "Speicher",
        "Bruttoleistung": 60000.0,
        "Nettonennleistung": 60000.0,
        "NutzbareSpeicherkapazitaet": 180500.0,
        "Stromspeichertechnologie": 524,
        "StromspeichertechnologieBezeichnung": "Batterie",
        "Batterietechnologie": 727,
        "SpeicherEinheitMastrNummer": "SSE900000000201",
        "TechnologieStromerzeugung": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def norm(cfg):
    return Normalizer(cfg, "mastr_json")


def test_live_battery_is_recognised(norm):
    r = norm.normalize(live_like())
    assert r.is_battery is True and r.in_scope
    assert "TECHNOLOGY_UNKNOWN" not in r.quality_flags
    assert r.usable_capacity_mwh == Decimal("180.5") and r.duration_h == Decimal("3.01")
    assert r.storage_mastr_id == "SSE900000000201"
    assert r.planned_commissioning_date.isoformat() == "2028-05-22"
    assert r.registration_date is not None
    assert r.federal_state == "Sachsen"


def test_live_pumped_hydro_is_excluded_by_label(norm):
    r = norm.normalize(live_like(Stromspeichertechnologie=1537, StromspeichertechnologieBezeichnung="Pumpspeicher",
                                 Batterietechnologie=None, Nettonennleistung=39700.0,
                                 NutzbareSpeicherkapazitaet=2308000.0))
    assert r.is_battery is False and not r.in_scope and r.scope_reason == "not_battery_technology"


def test_live_pumped_hydro_is_excluded_by_code_only(norm):
    r = norm.normalize(live_like(Stromspeichertechnologie=1537, StromspeichertechnologieBezeichnung=None,
                                 Batterietechnologie=None))
    assert r.is_battery is False and not r.in_scope


def test_live_battery_by_code_only(norm):
    r = norm.normalize(live_like(StromspeichertechnologieBezeichnung=None, Batterietechnologie=None))
    assert r.is_battery is True and r.in_scope


def test_negative_epoch_commissioning_date(norm):
    # live data contains 1960s pumped-hydro plants: /Date(-110678400000)/
    r = norm.normalize(live_like(InbetriebnahmeDatum="/Date(-110678400000)/"))
    assert r.commissioning_date.year == 1966
