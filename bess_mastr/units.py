"""Number, unit and timestamp parsing.

All quantities are handled as :class:`decimal.Decimal` so that the threshold
comparison (``> 10 MW``) is exact: 10 000 kW must never become
10.000000000001 MW because of binary floating point.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

POWER_TO_MW = {
    "w": Decimal("0.000001"),
    "kw": Decimal("0.001"),
    "mw": Decimal("1"),
    "gw": Decimal("1000"),
}
ENERGY_TO_MWH = {
    "wh": Decimal("0.000001"),
    "kwh": Decimal("0.001"),
    "mwh": Decimal("1"),
    "gwh": Decimal("1000"),
}
QUANT = Decimal("0.000001")  # 1 W / 1 Wh resolution once expressed in MW / MWh

_UNIT_SUFFIX = re.compile(r"^\s*(?P<num>[-+]?[\d.,\s ]+)\s*(?P<unit>[a-zA-Z]+)?\s*$")
_GROUPED_DOT_THOUSANDS = re.compile(r"^[-+]?\d{1,3}(\.\d{3})+$")
_GROUPED_COMMA_THOUSANDS = re.compile(r"^[-+]?\d{1,3}(,\d{3})+$")
_MS_DATE = re.compile(r"^/Date\((?P<ms>-?\d+)(?P<tz>[+-]\d{4})?\)/$")


class UnitError(ValueError):
    """Value could not be parsed into a number or quantity."""


def parse_decimal(value, locale: str = "de") -> Decimal | None:
    """Parse a number coming from MaStR JSON, XML or CSV.

    * ``None`` / ``""`` -> ``None`` (missing, not an error)
    * int / float / Decimal -> exact Decimal (floats via ``repr``)
    * strings: German ("12.500,5") and English ("12,500.5") grouping are both
      understood. A string with only dots in strict thousands groups
      (``"12.500"``) is read as German thousands grouping when
      ``locale == "de"`` because that is how the MaStR UI/CSV renders kW.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise UnitError(f"boolean is not a number: {value!r}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise UnitError(f"non-finite number: {value!r}")
        result = Decimal(repr(value))
    elif isinstance(value, str):
        s = value.strip().replace(" ", "").replace(" ", "")
        if s == "" or s.lower() in {"null", "none", "-"}:
            return None
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):  # 12.500,5 (German)
                s = s.replace(".", "").replace(",", ".")
            else:  # 12,500.5 (English)
                s = s.replace(",", "")
        elif "," in s:
            if locale != "de" and _GROUPED_COMMA_THOUSANDS.match(s):
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        elif "." in s and locale == "de" and _GROUPED_DOT_THOUSANDS.match(s):
            s = s.replace(".", "")
        try:
            result = Decimal(s)
        except InvalidOperation as exc:
            raise UnitError(f"not a number: {value!r}") from exc
    else:
        raise UnitError(f"unsupported type {type(value).__name__}: {value!r}")
    if not result.is_finite():
        raise UnitError(f"non-finite number: {value!r}")
    return result


def _split_unit(value) -> tuple[object, str | None]:
    if isinstance(value, str):
        m = _UNIT_SUFFIX.match(value)
        if m and m.group("unit"):
            return m.group("num"), m.group("unit").lower()
    return value, None


def to_mw(value, default_unit: str = "kW", locale: str = "de") -> Decimal | None:
    """Convert a power value to MW. Embedded units ("12,5 MW") win over the default."""
    raw, unit = _split_unit(value)
    unit = (unit or default_unit).lower()
    if unit not in POWER_TO_MW:
        raise UnitError(f"unknown power unit {unit!r} in {value!r}")
    number = parse_decimal(raw, locale)
    if number is None:
        return None
    return (number * POWER_TO_MW[unit]).quantize(QUANT)


def to_mwh(value, default_unit: str = "kWh", locale: str = "de") -> Decimal | None:
    raw, unit = _split_unit(value)
    unit = (unit or default_unit).lower()
    if unit not in ENERGY_TO_MWH:
        raise UnitError(f"unknown energy unit {unit!r} in {value!r}")
    number = parse_decimal(raw, locale)
    if number is None:
        return None
    return (number * ENERGY_TO_MWH[unit]).quantize(QUANT)


def parse_source_timestamp(value) -> datetime | None:
    """Parse MaStR timestamps into timezone-aware UTC datetimes.

    Supported: ASP.NET JSON dates (``/Date(1693526400000)/``, optional
    ``+0200`` suffix which does not change the instant), ISO-8601 strings
    (naive ones are treated as UTC), German ``dd.mm.yyyy`` dates and
    ``date``/``datetime`` objects.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if not isinstance(value, str):
        raise UnitError(f"unsupported timestamp type: {value!r}")
    s = value.strip()
    if not s:
        return None
    m = _MS_DATE.match(s)
    if m:
        # The millisecond value is already UTC; the optional offset is display info.
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(m.group("ms")))
    try:
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", s):
            d = datetime.strptime(s, "%d.%m.%Y")
            return d.replace(tzinfo=timezone.utc)
        # .NET exports use 7 fractional digits; Python accepts at most 6.
        s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise UnitError(f"unparseable timestamp: {value!r}") from exc
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_source_date(value) -> date | None:
    ts = parse_source_timestamp(value)
    return ts.date() if ts else None
