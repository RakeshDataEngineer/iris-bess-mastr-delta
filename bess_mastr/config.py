"""Configuration loading.

Config comes from a YAML file (see ``config/config.example.yaml``) with a
small set of environment-variable overrides so that secrets (the database
password) never have to live in a file that is committed.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

FORBIDDEN_SCHEMAS = {"iris_core", "public"}

DEFAULT_ENDPOINT = (
    "https://www.marktstammdatenregister.de/MaStR/Einheit/EinheitJson/"
    "GetErweiterteOeffentlicheEinheitStromerzeugung"
)


class ConfigError(ValueError):
    """Raised when configuration is invalid or unsafe."""


@dataclass(frozen=True)
class SourceConfig:
    base_url: str = DEFAULT_ENDPOINT
    # MaStR catalogue value for Energietraeger "Speicher" (storage).
    energietraeger_code: str = "2496"
    # Server-side pre-filter. Deliberately BELOW the scope threshold so that a
    # project which drops from >10 MW to e.g. 8 MW is still returned and can be
    # recorded as LEFT_SCOPE instead of silently disappearing.
    candidate_floor_kw: Decimal = Decimal("1000")
    # Filter-grid column labels used by the public MaStR unit table.
    power_filter_field: str = "Nettonennleistung der Einheit"
    carrier_filter_field: str = "Energieträger"
    updated_filter_field: str = "Datum der letzten Aktualisierung"
    sort: str = "MaStRNummer-asc"  # stable key => deterministic paging
    page_size: int = 500
    max_pages: int = 400
    timeout_s: float = 60.0
    max_retries: int = 4
    backoff_s: float = 2.0
    request_interval_s: float = 1.0
    overlap_days: int = 2
    user_agent: str = "iris-bess-mastr-delta/1.0 (candidate assignment; staging only)"
    power_unit: str = "kW"  # MaStR reports Nettonennleistung/Bruttoleistung in kW
    capacity_unit: str = "kWh"  # MaStR reports NutzbareSpeicherkapazitaet in kWh
    number_locale: str = "de"  # how to read *string* numbers ("12.500,5")


@dataclass(frozen=True)
class ScopeConfig:
    threshold_mw: Decimal = Decimal("10")  # strictly greater than
    include_unknown_technology: bool = True
    battery_technology_keywords: tuple[str, ...] = (
        "batterie", "battery", "lithium", "blei", "redox", "natrium",
        "nickel", "zink", "salzwasser",
    )
    non_battery_technology_keywords: tuple[str, ...] = (
        "pump", "druckluft", "schwungrad", "wasserstoff", "kondensator",
        "supraleit", "waerme", "wärme",
    )
    # MaStR catalogue codes for storage technology, confirmed on live data
    # (Stromspeichertechnologie: 524 = Batterie, 1537 = Pumpspeicher).
    battery_technology_codes: tuple[str, ...] = ("524",)
    non_battery_technology_codes: tuple[str, ...] = ("1537",)
    max_plausible_mw: Decimal = Decimal("2000")
    min_plausible_duration_h: Decimal = Decimal("0.25")
    max_plausible_duration_h: Decimal = Decimal("12")


@dataclass(frozen=True)
class DbConfig:
    url: str = "sqlite:///data/iris_staging.sqlite"
    schema: str = "iris_staging"
    deltas_table: str = "bess_pipeline_deltas"


@dataclass(frozen=True)
class AppConfig:
    source: SourceConfig = field(default_factory=SourceConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    db: DbConfig = field(default_factory=DbConfig)
    landing_dir: str = "data/landing"
    reports_dir: str = "reports"
    # "observed" for live MaStR pulls, "fixture" for synthetic test inputs.
    # Every stored row carries this so illustrative data can never be
    # mistaken for observational data.
    data_origin: str = "observed"

    def fingerprint(self) -> str:
        """Hash of the effective config, stored on every run for auditability."""
        payload = json.dumps(asdict(self), default=str, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _coerce(section_cls: type, raw: dict[str, Any] | None):
    raw = dict(raw or {})
    defaults = section_cls()
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if not hasattr(defaults, key):
            raise ConfigError(f"Unknown config key '{key}' in {section_cls.__name__}")
        current = getattr(defaults, key)
        if isinstance(current, Decimal):
            value = Decimal(str(value))
        elif isinstance(current, tuple):
            value = tuple(str(v) for v in value)
        kwargs[key] = value
    return section_cls(**kwargs)


def load_config(path: str | os.PathLike | None = None) -> AppConfig:
    raw: dict[str, Any] = {}
    if path:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"Config file not found: {p}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    db_raw = dict(raw.get("db") or {})
    if os.getenv("IRIS_DB_URL"):
        db_raw["url"] = os.environ["IRIS_DB_URL"]
    src_raw = dict(raw.get("source") or {})
    if os.getenv("MASTR_BASE_URL"):
        src_raw["base_url"] = os.environ["MASTR_BASE_URL"]

    cfg = AppConfig(
        source=_coerce(SourceConfig, src_raw),
        scope=_coerce(ScopeConfig, raw.get("scope")),
        db=_coerce(DbConfig, db_raw),
        landing_dir=raw.get("landing_dir", AppConfig.landing_dir),
        reports_dir=raw.get("reports_dir", AppConfig.reports_dir),
        data_origin=os.getenv("IRIS_DATA_ORIGIN", raw.get("data_origin", "observed")),
    )
    validate_config(cfg)
    return cfg


def validate_config(cfg: AppConfig) -> None:
    if cfg.db.schema.lower() in FORBIDDEN_SCHEMAS:
        raise ConfigError(
            f"Refusing to write to schema '{cfg.db.schema}': this pipeline is staging-only"
        )
    if cfg.data_origin not in {"observed", "fixture"}:
        raise ConfigError("data_origin must be 'observed' or 'fixture'")
    if cfg.scope.threshold_mw <= 0:
        raise ConfigError("threshold_mw must be positive")
    if cfg.source.candidate_floor_kw > cfg.scope.threshold_mw * 1000:
        raise ConfigError(
            "candidate_floor_kw must not exceed the scope threshold, otherwise "
            "projects leaving scope cannot be detected"
        )
    if cfg.source.page_size < 1 or cfg.source.max_pages < 1:
        raise ConfigError("page_size and max_pages must be >= 1")
