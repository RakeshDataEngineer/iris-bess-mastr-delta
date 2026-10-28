"""Normalised record and rejection models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Fields that define "the project changed". Bookkeeping (timestamps of the
# source row itself, raw-payload hash, flags) is deliberately excluded so a
# bumped DatumLetzteAktualisierung without a real change does not create noise.
BUSINESS_FIELDS: tuple[str, ...] = (
    "mastr_id",
    "unit_name",
    "operator_name",
    "storage_mastr_id",
    "energy_carrier",
    "storage_technology",
    "is_battery",
    "net_power_mw",
    "gross_power_mw",
    "power_basis",
    "usable_capacity_mwh",
    "status",
    "status_code",
    "commissioning_date",
    "planned_commissioning_date",
    "postal_code",
    "city",
    "federal_state",
    "municipality_key",
    "latitude",
    "longitude",
)


def canonical(value: Any) -> Any:
    """JSON-safe, deterministic representation used for hashing and storage."""
    if isinstance(value, Decimal):
        # normalize() makes 50.000000 and 50 hash identically; "f" avoids 5E+1.
        return format(value.normalize(), "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    return value


def stable_hash(obj: Any) -> str:
    data = json.dumps(canonical(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NormalizedRecord:
    mastr_id: str
    unit_name: str | None
    operator_name: str | None
    storage_mastr_id: str | None
    energy_carrier: str | None
    storage_technology: str | None
    is_battery: bool | None
    net_power_mw: Decimal | None
    gross_power_mw: Decimal | None
    scope_power_mw: Decimal
    power_basis: str  # "net" | "gross"
    usable_capacity_mwh: Decimal | None
    duration_h: Decimal | None
    status: str
    status_code: str | None
    status_label_source: str | None
    commissioning_date: date | None
    planned_commissioning_date: date | None
    registration_date: date | None
    source_updated_at: datetime | None
    postal_code: str | None
    city: str | None
    federal_state: str | None
    municipality_key: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    location_quality: str
    in_scope: bool
    scope_reason: str
    quality_flags: tuple[str, ...]
    source_system: str
    payload_sha256: str
    record_hash: str = field(default="")

    def business_view(self) -> dict[str, Any]:
        return {k: canonical(getattr(self, k)) for k in BUSINESS_FIELDS}

    def as_dict(self) -> dict[str, Any]:
        return canonical(asdict(self))

    @property
    def geometry_wkt(self) -> str | None:
        if self.latitude is None or self.longitude is None or self.location_quality != "ok":
            return None
        return f"POINT({self.longitude} {self.latitude})"


@dataclass(frozen=True)
class RejectedRecord:
    reason_code: str
    detail: str
    payload_sha256: str
    mastr_id_raw: str | None
    payload: Any
    source_system: str
