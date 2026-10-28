"""Raw MaStR record -> NormalizedRecord (or RejectedRecord).

The normaliser accepts both shapes the pipeline can ingest:

* public JSON unit table (``MaStRNummer``, ``Nettonennleistung``, ...)
* bulk XML export, ``EinheitenStromSpeicher`` joined with
  ``AnlagenStromSpeicher`` (``EinheitMastrNummer``, ...)

Policy: a record is *rejected* only when it cannot be identified or its
power cannot be determined (it is then impossible to decide scope).
Everything else (missing coordinates, missing MWh, unknown technology...) is
kept and tagged with a quality flag, so no information is lost.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Any, Iterable

from .config import AppConfig
from .models import NormalizedRecord, RejectedRecord, stable_hash
from .units import UnitError, parse_decimal, parse_source_date, parse_source_timestamp, to_mw, to_mwh

MASTR_UNIT_ID = re.compile(r"^SEE\d{12}$")

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "mastr_id": ("MaStRNummer", "EinheitMastrNummer"),
    "unit_name": ("EinheitName", "NameStromerzeugungseinheit"),
    "operator_name": ("AnlagenbetreiberName",),
    "storage_mastr_id": ("SpeicherEinheitMastrNummer", "SpeMastrNummer", "SpeicherMaStRNummer"),
    "energy_carrier": ("EnergietraegerName", "Energietraeger"),
    "net_power": ("Nettonennleistung",),
    "gross_power": ("Bruttoleistung",),
    "capacity": (
        "NutzbareSpeicherkapazitaet",
        "SpeicherNutzbareSpeicherkapazitaet",
        "NutzbareSpeicherkapazitaetInKwh",
    ),
    "status_id": ("BetriebsStatusId", "EinheitBetriebsstatus"),
    "status_name": ("BetriebsStatusName",),
    "commissioning": ("InbetriebnahmeDatum", "Inbetriebnahmedatum"),
    # sic: the public table spells it "Inbetriebsnahme"
    "planned_commissioning": ("GeplantesInbetriebsnahmeDatum", "GeplantesInbetriebnahmeDatum",
                              "GeplantesInbetriebnahmedatum"),
    "registration": ("EinheitRegistrierungsdatum", "EinheitMeldeDatum", "Registrierungsdatum"),
    "updated": ("DatumLetzteAktualisierung",),
    "postal_code": ("Plz", "Postleitzahl"),
    "city": ("Ort",),
    "federal_state": ("BundeslandName", "Bundesland"),
    "municipality_key": ("Gemeindeschluessel",),
    "latitude": ("Breitengrad",),
    "longitude": ("Laengengrad",),
}
# Order matters only for display. Verified against live MaStR records
# (2026-10-28): the public table returns Stromspeichertechnologie (code,
# 524 = Batterie, 1537 = Pumpspeicher) and StromspeichertechnologieBezeichnung
# (label); Batterietechnologie is a battery-chemistry code (e.g. 727).
TECHNOLOGY_FIELDS = (
    "StromspeichertechnologieBezeichnung", "Stromspeichertechnologie",
    "TechnologieName", "Technologie", "TechnologieDerStromspeicherung",
    "BatterietechnologieName", "Batterietechnologie",
)

STATUS_BY_CODE = {
    "31": "planned",
    "35": "operating",
    "37": "temporarily_shut_down",
    "38": "permanently_shut_down",
}
STATUS_BY_LABEL = {
    "in planung": "planned",
    "in betrieb": "operating",
    "vorübergehend stillgelegt": "temporarily_shut_down",
    "voruebergehend stillgelegt": "temporarily_shut_down",
    "endgültig stillgelegt": "permanently_shut_down",
    "endgueltig stillgelegt": "permanently_shut_down",
}
STORAGE_CARRIER_LABELS = {"speicher", "storage"}

# Generous bounding box around Germany (incl. offshore zone).
DE_BBOX = (Decimal("47.0"), Decimal("55.5"), Decimal("5.5"), Decimal("15.5"))


def payload_sha256(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _first(raw: dict, key: str):
    for alias in FIELD_ALIASES[key]:
        if alias in raw and raw[alias] not in (None, ""):
            return raw[alias]
    return None


def _text(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


class Normalizer:
    def __init__(self, cfg: AppConfig, source_system: str, number_locale: str | None = None):
        self.cfg = cfg
        self.source_system = source_system
        # JSON numbers are unambiguous; the locale only matters for numeric
        # *strings*. The bulk XML export always uses "." as decimal separator.
        self.locale = number_locale or cfg.source.number_locale

    # ------------------------------------------------------------------
    def normalize(self, raw: Any) -> NormalizedRecord | RejectedRecord:
        sha = payload_sha256(raw)
        if not isinstance(raw, dict):
            return self._reject("NOT_A_RECORD", f"expected object, got {type(raw).__name__}", sha, None, raw)

        mastr_raw = _text(_first(raw, "mastr_id"))
        if mastr_raw is None:
            return self._reject("MISSING_MASTR_ID", "no MaStRNummer/EinheitMastrNummer", sha, None, raw)
        mastr_id = mastr_raw.upper()
        if not MASTR_UNIT_ID.match(mastr_id):
            return self._reject("INVALID_MASTR_ID", f"'{mastr_raw}' is not SEE + 12 digits", sha, mastr_raw, raw)

        flags: list[str] = []
        src, scope = self.cfg.source, self.cfg.scope

        # ---- power ---------------------------------------------------
        try:
            net_mw = to_mw(_first(raw, "net_power"), src.power_unit, self.locale)
            gross_mw = to_mw(_first(raw, "gross_power"), src.power_unit, self.locale)
        except UnitError as exc:
            return self._reject("MALFORMED_POWER", str(exc), sha, mastr_id, raw)
        if net_mw is None and gross_mw is None:
            return self._reject("MISSING_POWER", "neither Nettonennleistung nor Bruttoleistung present", sha, mastr_id, raw)
        if net_mw is not None:
            scope_mw, basis = net_mw, "net"
        else:
            scope_mw, basis = gross_mw, "gross"
            flags.append("POWER_FROM_GROSS")
        if scope_mw < 0:
            return self._reject("NEGATIVE_POWER", f"{scope_mw} MW", sha, mastr_id, raw)
        if scope_mw > scope.max_plausible_mw:
            return self._reject(
                "IMPLAUSIBLE_POWER",
                f"{scope_mw} MW exceeds {scope.max_plausible_mw} MW (likely unit error W vs kW)",
                sha, mastr_id, raw,
            )
        if net_mw is not None and gross_mw is not None and net_mw > gross_mw * Decimal("1.05"):
            flags.append("NET_EXCEEDS_GROSS")

        # ---- energy ----------------------------------------------------
        capacity_mwh = None
        try:
            capacity_mwh = to_mwh(_first(raw, "capacity"), src.capacity_unit, self.locale)
        except UnitError:
            flags.append("CAPACITY_MALFORMED")
        if capacity_mwh is None and "CAPACITY_MALFORMED" not in flags:
            flags.append("CAPACITY_MISSING")
        if capacity_mwh is not None and capacity_mwh < 0:
            capacity_mwh = None
            flags.append("CAPACITY_NEGATIVE")
        duration_h = None
        if capacity_mwh is not None and scope_mw > 0:
            duration_h = (capacity_mwh / scope_mw).quantize(Decimal("0.01"))
            if not (scope.min_plausible_duration_h <= duration_h <= scope.max_plausible_duration_h):
                flags.append("DURATION_IMPLAUSIBLE")

        # ---- carrier / technology ----------------------------------------
        carrier = _text(_first(raw, "energy_carrier"))
        is_storage = None
        if carrier is not None:
            is_storage = carrier.lower() in STORAGE_CARRIER_LABELS or carrier == src.energietraeger_code
            if not is_storage:
                flags.append("NOT_STORAGE_CARRIER")
        else:
            flags.append("CARRIER_MISSING")

        tech_values = [_text(raw.get(f)) for f in TECHNOLOGY_FIELDS]
        tech_values = [t for t in tech_values if t]
        technology = " / ".join(dict.fromkeys(tech_values)) or None
        is_battery = self._classify_battery(tech_values)
        if is_battery is None:
            flags.append("TECHNOLOGY_UNKNOWN")

        # ---- status --------------------------------------------------------
        status_code = _text(_first(raw, "status_id"))
        status_label = _text(_first(raw, "status_name"))
        status = STATUS_BY_CODE.get(status_code or "")
        if status is None and status_label:
            status = STATUS_BY_LABEL.get(status_label.lower())
        if status is None:
            status = "unknown"
            flags.append("STATUS_UNKNOWN")

        # ---- timestamps -----------------------------------------------------
        def _date(key):
            try:
                return parse_source_date(_first(raw, key))
            except UnitError:
                flags.append(f"{key.upper()}_UNPARSEABLE")
                return None

        commissioning = _date("commissioning")
        planned = _date("planned_commissioning")
        registration = _date("registration")
        try:
            updated_at = parse_source_timestamp(_first(raw, "updated"))
        except UnitError:
            updated_at = None
            flags.append("UPDATED_AT_UNPARSEABLE")
        if updated_at is None and "UPDATED_AT_UNPARSEABLE" not in flags:
            flags.append("UPDATED_AT_MISSING")

        # ---- location ---------------------------------------------------------
        lat = lon = None
        try:
            lat = parse_decimal(_first(raw, "latitude"), locale="en")
            lon = parse_decimal(_first(raw, "longitude"), locale="en")
        except UnitError:
            lat = lon = None
            flags.append("LOCATION_MALFORMED")
        if lat is None or lon is None:
            location_quality = "missing"
            lat = lon = None
            if "LOCATION_MALFORMED" not in flags:
                flags.append("LOCATION_MISSING")
        elif not (DE_BBOX[0] <= lat <= DE_BBOX[1] and DE_BBOX[2] <= lon <= DE_BBOX[3]):
            location_quality = "out_of_bounds"
            flags.append("LOCATION_OUT_OF_BOUNDS")
        else:
            location_quality = "ok"
        if lat is not None:
            lat, lon = lat.quantize(Decimal("0.000001")), lon.quantize(Decimal("0.000001"))

        # ---- scope --------------------------------------------------------------
        in_scope, reason = self._decide_scope(scope_mw, is_storage, is_battery)

        record = NormalizedRecord(
            mastr_id=mastr_id,
            unit_name=_text(_first(raw, "unit_name")),
            operator_name=_text(_first(raw, "operator_name")),
            storage_mastr_id=_text(_first(raw, "storage_mastr_id")),
            energy_carrier=carrier,
            storage_technology=technology,
            is_battery=is_battery,
            net_power_mw=net_mw,
            gross_power_mw=gross_mw,
            scope_power_mw=scope_mw,
            power_basis=basis,
            usable_capacity_mwh=capacity_mwh,
            duration_h=duration_h,
            status=status,
            status_code=status_code,
            status_label_source=status_label,
            commissioning_date=commissioning,
            planned_commissioning_date=planned,
            registration_date=registration,
            source_updated_at=updated_at,
            postal_code=_text(_first(raw, "postal_code")),
            city=_text(_first(raw, "city")),
            federal_state=_text(_first(raw, "federal_state")),
            municipality_key=_text(_first(raw, "municipality_key")),
            latitude=lat,
            longitude=lon,
            location_quality=location_quality,
            in_scope=in_scope,
            scope_reason=reason,
            quality_flags=tuple(sorted(set(flags))),
            source_system=self.source_system,
            payload_sha256=sha,
        )
        return _with_hash(record)

    # ------------------------------------------------------------------
    def _classify_battery(self, tech_values: Iterable[str]) -> bool | None:
        scope = self.cfg.scope
        verdict = None
        for value in tech_values:
            low = value.lower()
            if value in scope.non_battery_technology_codes or any(k in low for k in scope.non_battery_technology_keywords):
                return False
            if value in scope.battery_technology_codes or any(k in low for k in scope.battery_technology_keywords):
                verdict = True
        return verdict

    def _decide_scope(self, power_mw: Decimal, is_storage, is_battery) -> tuple[bool, str]:
        threshold = self.cfg.scope.threshold_mw
        if is_storage is False:
            return False, "not_storage"
        if is_battery is False:
            return False, "not_battery_technology"
        if is_battery is None and not self.cfg.scope.include_unknown_technology:
            return False, "technology_unknown_excluded"
        if power_mw <= threshold:
            return False, f"power_not_above_{threshold}mw"
        return True, "battery_above_threshold" if is_battery else "storage_above_threshold_technology_unverified"

    def _reject(self, code, detail, sha, mastr_id, raw) -> RejectedRecord:
        return RejectedRecord(code, detail, sha, mastr_id, raw, self.source_system)


def _with_hash(record: NormalizedRecord) -> NormalizedRecord:
    object.__setattr__(record, "record_hash", stable_hash(record.business_view()))
    return record
