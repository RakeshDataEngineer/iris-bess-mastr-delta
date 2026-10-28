from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bess_mastr.units import UnitError, parse_decimal, parse_source_timestamp, to_mw, to_mwh


@pytest.mark.parametrize("value, unit, expected", [
    (10000, "kW", Decimal("10")),            # exactly the threshold
    (10000.5, "kW", Decimal("10.0005")),     # float from JSON
    ("10.000,5", "kW", Decimal("10.0005")),  # German string
    ("10,000.5", "kW", Decimal("10.0005")),  # English string
    ("12.500", "kW", Decimal("12.5")),       # German thousands grouping
    ("12,5 MW", "kW", Decimal("12.5")),      # embedded unit wins
    ("12500 kW", "MW", Decimal("12.5")),
    ("0.05 GW", "kW", Decimal("50")),
    (25_000_000, "W", Decimal("25")),
    (Decimal("9999"), "kW", Decimal("9.999")),
    (None, "kW", None),
    ("", "kW", None),
])
def test_to_mw(value, unit, expected):
    assert to_mw(value, unit) == expected


def test_threshold_is_exact_not_float():
    # 0.1 + 0.2 style artefacts must not push 10 000 kW above 10 MW
    assert to_mw(10000.0, "kW") == Decimal("10")
    assert not to_mw(10000.0, "kW") > Decimal("10")


def test_to_mwh():
    assert to_mwh(100000, "kWh") == Decimal("100")
    assert to_mwh("20.001,0", "kWh") == Decimal("20.001")
    assert to_mwh("1,5 GWh") == Decimal("1500")


@pytest.mark.parametrize("bad", ["n/a", "12 apples", True, [1], float("nan")])
def test_garbage_power_raises(bad):
    with pytest.raises(UnitError):
        to_mw(bad, "kW")


def test_english_locale_keeps_dot_decimal():
    assert parse_decimal("10.000", locale="en") == Decimal("10")
    assert parse_decimal("10.000", locale="de") == Decimal("10000")


@pytest.mark.parametrize("value, expected", [
    ("/Date(1790812800000)/", datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ("/Date(1790812800000+0200)/", datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ("2026-10-01T02:00:00+02:00", datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ("2026-10-01", datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ("01.10.2026", datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ("2026-09-30T08:15:00.1234567", None),  # 7 fractional digits from bulk export
    (None, None),
])
def test_timestamps(value, expected):
    result = parse_source_timestamp(value)
    if value == "2026-09-30T08:15:00.1234567":
        assert result.tzinfo is not None and result.year == 2026
        return
    assert result == expected


def test_bad_timestamp_raises():
    with pytest.raises(UnitError):
        parse_source_timestamp("yesterday")
