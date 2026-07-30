"""Unit handling shared by the RFQ decoder and the pricing engine.

Allan Edwards quotes, part numbers, and price tables are entirely imperial, but
RFQs arrive from outside North America stating every dimension in millimetres or
metres. An unconverted metric dimension does not read as *wrong* to the pricing
layer — it reads as *absent*, and the pricing layer then substitutes a default.
That is the failure behind task 330: a stated carrier pipe wall thickness of
12.7 mm (exactly 1/2") was quoted as the 3/8" default, because ``float("12.7 mm")``
raises and the field became ``None``.

This module is imported by both :mod:`allenedwards.parser` and
:mod:`allenedwards.pricing`, so it must not import either of them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

MM_PER_INCH = Decimal("25.4")
INCHES_PER_FOOT = Decimal("12")

# Canonical units we understand, and how many millimetres each metric one is.
METRIC_UNITS = frozenset({"mm", "cm", "m"})
_UNIT_TO_MM: dict[str, Decimal] = {
    "mm": Decimal("1"),
    "cm": Decimal("10"),
    "m": Decimal("1000"),
}

_UNIT_ALIASES: dict[str, str] = {
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "cm": "cm",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "in": "in",
    "inch": "in",
    "inches": "in",
    '"': "in",
    "''": "in",
    "ft": "ft",
    "foot": "ft",
    "feet": "ft",
    "'": "ft",
}

# Longest-first so "mm" wins over "m" and "metres" over "metre".
_UNIT_ALTERNATION = (
    "millimetres|millimeters|millimetre|millimeter|"
    "centimetres|centimeters|centimetre|centimeter|"
    "metres|meters|metre|meter|"
    "inches|inch|feet|foot|"
    "mm|cm|in|ft|m|"
    "''|\"|'"
)

_NUMBER = r"\d[\d,]*(?:\.\d+)?"

# A number followed by an explicit unit. The trailing lookahead keeps "m" from
# matching the leading letter of an unrelated word (e.g. "6,895 kPag" has no
# unit match at all, and "3 metres" matches "metres" rather than "m").
_MEASUREMENT_RE = re.compile(
    rf"(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT_ALTERNATION})(?![a-z])",
    re.IGNORECASE,
)

# "1/2", "6-5/8", "6 5/8" — the LLM sometimes emits these verbatim, and
# float() rejects them just as surely as it rejects "12.7 mm".
_FRACTION_RE = re.compile(
    r"^(?P<whole>\d+)?\s*[-\s]?\s*(?P<numerator>\d+)\s*/\s*(?P<denominator>\d+)$"
)

# Bare numbers carry no unit, so a magnitude test is the only signal available.
# Both thresholds sit far above anything Allan Edwards actually makes, so a
# value past them is metric rather than an implausible imperial dimension.
# Largest catalog wall thickness is 1"; largest catalog diameter is 48".
BARE_METRIC_THICKNESS_MIN_INCHES = Decimal("2")
BARE_METRIC_DIAMETER_MIN_INCHES = Decimal("60")


# --------------------------------------------------------------------------
# Quote-style fractions
#
# These tables define every dimension Allan Edwards will print on a quote, so
# a converted metric value has to land ON one of them — carrying a raw decimal
# like 0.49999 through to a part number would invent a size that does not exist.
# --------------------------------------------------------------------------

COMMON_SUBINCH_FRACTIONS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("0"), "0"),
    (Decimal("0.125"), "1/8"),
    (Decimal("0.1875"), "3/16"),
    (Decimal("0.25"), "1/4"),
    (Decimal("0.28125"), "9/32"),
    (Decimal("0.3125"), "5/16"),
    (Decimal("0.34375"), "11/32"),
    (Decimal("0.375"), "3/8"),
    (Decimal("0.4375"), "7/16"),
    (Decimal("0.5"), "1/2"),
    (Decimal("0.5625"), "9/16"),
    (Decimal("0.625"), "5/8"),
    (Decimal("0.6875"), "11/16"),
    (Decimal("0.75"), "3/4"),
    (Decimal("0.8125"), "13/16"),
    (Decimal("0.875"), "7/8"),
    (Decimal("0.9375"), "15/16"),
    (Decimal("1"), "1"),
)

COMMON_LARGE_DIMENSION_FRACTIONS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("0"), ""),
    (Decimal("0.125"), "1/8"),
    (Decimal("0.25"), "1/4"),
    (Decimal("0.375"), "3/8"),
    (Decimal("0.5"), "1/2"),
    (Decimal("0.625"), "5/8"),
    (Decimal("0.75"), "3/4"),
    (Decimal("0.875"), "7/8"),
    (Decimal("1"), "1"),
)


def _format_decimal_inches(value: float | Decimal) -> str:
    decimal_value = Decimal(str(value))
    return f"{decimal_value:.3f}".rstrip("0").rstrip(".")


def _nearest_fraction(
    value: Decimal, options: tuple[tuple[Decimal, str], ...]
) -> tuple[Decimal, str]:
    return min(options, key=lambda option: (abs(value - option[0]), option[0]))


def _nearest_fraction_label(value: Decimal, options: tuple[tuple[Decimal, str], ...]) -> str:
    return _nearest_fraction(value, options)[1]


def decimal_to_fraction(value: float | Decimal) -> str:
    """Format decimal inch measurements as reduced quote-style fractions."""
    decimal_value = Decimal(str(value))
    sign = "-" if decimal_value < 0 else ""
    absolute_value = abs(decimal_value)
    if absolute_value < 1:
        return f"{sign}{_nearest_fraction_label(absolute_value, COMMON_SUBINCH_FRACTIONS)}"

    whole = int(absolute_value)
    remainder = absolute_value - Decimal(whole)
    fraction_text = _nearest_fraction_label(remainder, COMMON_LARGE_DIMENSION_FRACTIONS)

    if fraction_text == "1":
        return f"{sign}{whole + 1}"
    if not fraction_text:
        return f"{sign}{whole}"
    return f"{sign}{whole}-{fraction_text}"


def snap_to_catalog_fraction(value: float | Decimal) -> float:
    """Snap an inch measurement to the fraction a quote would actually print.

    This is the numeric twin of :func:`decimal_to_fraction` and deliberately
    mirrors its branch structure, so the stored number always agrees with the
    printed label. 12.7 mm converts to 0.5 exactly, but 1/32" rounding noise in
    a source document (say 12.6 mm) must still land on a real catalog size.
    """
    decimal_value = Decimal(str(value))
    sign = Decimal("-1") if decimal_value < 0 else Decimal("1")
    absolute_value = abs(decimal_value)

    if absolute_value < 1:
        snapped = _nearest_fraction(absolute_value, COMMON_SUBINCH_FRACTIONS)[0]
    else:
        whole = Decimal(int(absolute_value))
        remainder = absolute_value - whole
        snapped = whole + _nearest_fraction(remainder, COMMON_LARGE_DIMENSION_FRACTIONS)[0]

    return float(sign * snapped)


# --------------------------------------------------------------------------
# Parsing values that may carry units
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Measurement:
    """A numeric value plus the unit it was stated in."""

    value: Decimal
    unit: str
    source_text: str

    @property
    def is_metric(self) -> bool:
        return self.unit in METRIC_UNITS

    def to_inches(self) -> Decimal:
        if self.unit in _UNIT_TO_MM:
            return self.value * _UNIT_TO_MM[self.unit] / MM_PER_INCH
        if self.unit == "ft":
            return self.value * INCHES_PER_FOOT
        return self.value

    def to_feet(self) -> Decimal:
        # A unit-less value is NOT inches here: the only field measured in feet is
        # ``length_ft``, whose bare numbers are already feet. Reading them as
        # inches would quote a 10 ft sleeve as 10 in.
        if self.unit in ("ft", ""):
            return self.value
        return self.to_inches() / INCHES_PER_FOOT

    def describe(self) -> str:
        """Render the measurement the way the source stated it, for line notes."""
        return self.source_text.strip()


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "").strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _canonical_unit(unit: str) -> str | None:
    return _UNIT_ALIASES.get(unit.strip().lower())


def _parse_fraction(text: str) -> Decimal | None:
    """Parse '1/2', '6-5/8', '6 5/8' into a Decimal."""
    match = _FRACTION_RE.match(text.strip())
    if not match:
        return None
    denominator = _to_decimal(match.group("denominator"))
    numerator = _to_decimal(match.group("numerator"))
    if not denominator or numerator is None:
        return None
    whole = _to_decimal(match.group("whole") or "0") or Decimal("0")
    return whole + numerator / denominator


def parse_measurement(raw: Any) -> Measurement | None:
    """Parse a raw RFQ field that may carry a unit suffix.

    Handles plain numbers (``12.7``, ``"0.5"``), unit-bearing strings
    (``"12.7 mm"``, ``"3,000 mm"``, ``"3.0 metres"``, ``'1/2"'``) and bare
    fractions (``"1/2"``, ``"6-5/8"``). Returns ``None`` when no number is
    present at all. A value with no unit is reported with unit ``""`` so the
    caller can apply its own dimension-specific interpretation.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Measurement(Decimal(str(raw)), "", str(raw))

    text = str(raw).strip()
    if not text:
        return None

    match = _MEASUREMENT_RE.search(text)
    if match:
        value = _to_decimal(match.group("value"))
        unit = _canonical_unit(match.group("unit"))
        if value is not None and unit:
            # A fraction-with-unit ('1/2"') would otherwise parse as just "2".
            stripped = text
            for suffix_unit in ('"', "''", "'"):
                if stripped.endswith(suffix_unit):
                    stripped = stripped[: -len(suffix_unit)].strip()
                    break
            fraction = _parse_fraction(stripped)
            if fraction is not None and unit == "in":
                return Measurement(fraction, unit, text)
            return Measurement(value, unit, match.group(0).strip())

    fraction = _parse_fraction(text)
    if fraction is not None:
        return Measurement(fraction, "", text)

    plain = _to_decimal(text)
    if plain is not None:
        return Measurement(plain, "", text)

    return None


# --------------------------------------------------------------------------
# Dimension-specific conversion
# --------------------------------------------------------------------------


def convert_thickness_to_inches(raw: Any) -> tuple[float | None, str | None]:
    """Resolve a wall thickness to snapped inches.

    Returns ``(inches, note)`` where ``note`` describes any metric conversion
    so the quote line can show its work. ``(None, None)`` means no usable value.
    """
    measurement = parse_measurement(raw)
    if measurement is None:
        return None, None

    unit = measurement.unit
    if not unit and measurement.value > BARE_METRIC_THICKNESS_MIN_INCHES:
        # No unit given, but no Allan Edwards sleeve has a wall this thick in
        # inches, so the RFQ meant millimetres.
        measurement = Measurement(measurement.value, "mm", f"{measurement.value:g} mm")
        unit = "mm"

    inches = measurement.to_inches()
    if inches <= 0:
        return None, None

    snapped = snap_to_catalog_fraction(inches)
    if not measurement.is_metric:
        return snapped, None

    note = (
        f'wall thickness {measurement.describe()} converted to '
        f'{decimal_to_fraction(snapped)}"'
    )
    return snapped, note


def convert_diameter_to_inches(raw: Any) -> tuple[float | None, str | None]:
    """Resolve a pipe diameter to inches, converting metric input."""
    measurement = parse_measurement(raw)
    if measurement is None:
        return None, None

    unit = measurement.unit
    if not unit and measurement.value > BARE_METRIC_DIAMETER_MIN_INCHES:
        measurement = Measurement(measurement.value, "mm", f"{measurement.value:g} mm")
        unit = "mm"

    inches = measurement.to_inches()
    if inches <= 0:
        return None, None

    if not measurement.is_metric:
        return float(inches), None

    snapped = snap_to_catalog_fraction(inches)
    note = (
        f'diameter {measurement.describe()} converted to '
        f'{decimal_to_fraction(snapped)}"'
    )
    return snapped, note


# --------------------------------------------------------------------------
# Metric length policy
#
# INTERIM, pending Chip Edwards' confirmation (task 330 scope item 2). A metric
# length rarely lands on a standard stock piece: 3,000 mm is 9.843 ft. The
# policy below is the ONLY place that decision is made — the LLM prompt is
# explicitly told NOT to pre-round metric lengths, so this constant cannot
# drift out of sync with a second copy of the rule.
#
#   "round_to_standard" — quote the nearest standard piece length when the
#                         converted value is within the tolerance below, and
#                         stamp the conversion on the line. (interim default)
#   "literal"           — always quote the converted length verbatim (9.843 ft).
#
# Either way a converted length that is NOT close to a standard piece is quoted
# literally and flagged, never silently reshaped.
# --------------------------------------------------------------------------

METRIC_LENGTH_POLICY = "round_to_standard"
METRIC_LENGTH_ROUNDING_TOLERANCE_FT = Decimal("1")


def convert_length_to_feet(
    raw: Any, standard_length_ft: float | None = None
) -> tuple[float | None, str | None]:
    """Resolve a length to feet, applying the metric length policy.

    ``standard_length_ft`` is the stock piece length for the product (10 ft for
    sleeves, 6 ft for girth welds). Returns ``(feet, note)``.
    """
    measurement = parse_measurement(raw)
    if measurement is None:
        return None, None

    # A bare number in a ``length_ft`` field already means feet; there is no
    # safe magnitude test here, because a large value legitimately means a
    # requested TOTAL footage that the pricing layer maps to piece counts.
    feet = measurement.to_feet()
    if feet <= 0:
        return None, None

    if not measurement.is_metric:
        return float(feet), None

    return _apply_metric_length_policy(feet, measurement, standard_length_ft)


def _apply_metric_length_policy(
    feet: Decimal, measurement: Measurement, standard_length_ft: float | None
) -> tuple[float, str]:
    exact_ft = feet.quantize(Decimal("0.001"))

    if (
        METRIC_LENGTH_POLICY == "round_to_standard"
        and standard_length_ft is not None
        and abs(feet - Decimal(str(standard_length_ft))) <= METRIC_LENGTH_ROUNDING_TOLERANCE_FT
    ):
        note = (
            f"length {measurement.describe()} ({exact_ft:f} ft) quoted as "
            f"standard {standard_length_ft:g} ft piece"
        )
        return float(standard_length_ft), note

    note = f"length {measurement.describe()} converted to {exact_ft:f} ft, verify"
    return float(exact_ft), note


# --------------------------------------------------------------------------
# Recovering dimensions the LLM dropped entirely
#
# When the LLM cannot map a metric dimension it sometimes returns null rather
# than the raw text, so the converters above never see it. These labelled
# scanners read the dimension back out of the preserved description. They match
# only LABELLED measurements, so the diameter ("914.4 mm") can never be
# mistaken for the wall thickness ("12.7 mm") in the same spec block.
# --------------------------------------------------------------------------

_THICKNESS_LABEL = r"(?:wall\s*thickness|wall\s*thk|wall|w\s*/\s*t|w\.?t\.?|thickness)"
_LENGTH_LABEL = r"(?:length|long|lg\.?)"
_DIAMETER_LABEL = r"(?:nominal\s*size|diameter|dia\.?|o\.?d\.?|nps|size)"


def _labelled_metric_finder(label: str) -> re.Pattern[str]:
    return re.compile(
        rf"\b{label}\b[^0-9\n]{{0,30}}(?P<value>{_NUMBER})\s*"
        rf"(?P<unit>millimetres|millimeters|millimetre|millimeter|"
        rf"centimetres|centimeters|centimetre|centimeter|"
        rf"metres|meters|metre|meter|mm|cm|m)(?![a-z])",
        re.IGNORECASE,
    )


_THICKNESS_METRIC_RE = _labelled_metric_finder(_THICKNESS_LABEL)
_LENGTH_METRIC_RE = _labelled_metric_finder(_LENGTH_LABEL)
_DIAMETER_METRIC_RE = _labelled_metric_finder(_DIAMETER_LABEL)


def _find_labelled_metric(pattern: re.Pattern[str], text: str) -> Measurement | None:
    if not text:
        return None
    match = pattern.search(text)
    if not match:
        return None
    value = _to_decimal(match.group("value"))
    unit = _canonical_unit(match.group("unit"))
    if value is None or unit not in METRIC_UNITS:
        return None
    return Measurement(value, unit, f"{match.group('value')} {match.group('unit')}")


def find_metric_thickness(text: str) -> tuple[float | None, str | None]:
    """Recover a labelled metric wall thickness from free text."""
    measurement = _find_labelled_metric(_THICKNESS_METRIC_RE, text)
    if measurement is None:
        return None, None
    return convert_thickness_to_inches(measurement.describe())


def find_metric_diameter(text: str) -> tuple[float | None, str | None]:
    """Recover a labelled metric diameter from free text."""
    measurement = _find_labelled_metric(_DIAMETER_METRIC_RE, text)
    if measurement is None:
        return None, None
    return convert_diameter_to_inches(measurement.describe())


def find_metric_length(
    text: str, standard_length_ft: float | None = None
) -> tuple[float | None, str | None]:
    """Recover a labelled metric length from free text."""
    measurement = _find_labelled_metric(_LENGTH_METRIC_RE, text)
    if measurement is None:
        return None, None
    return convert_length_to_feet(measurement.describe(), standard_length_ft)
