"""OmegaWrap composite calculator (T525, phase 1).

The deterministic parts of Maxim Dessiatov's Composite Code Reader
(docs/reference/maxim_combined_6000_dig.py, ~lines 29429-29844), ported
without the OCR: pipe-grade/SMYS mapping (S456), class-location mapping
(S457), and the fabric pick rule (S443).

The actual Linear Length of Repair formulas live in the AE Composite
Calculator Excel file (AE_Composite_Calculator.xlsm), which we do not have
yet. They are exposed behind FormulaEngine; the only engine today is
PendingExcelEngine, which returns no numbers. NEVER invent or approximate
those formulas here — they must be ported from the xlsm and verified
cell-for-cell against Excel output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# S456: the calculator's Pipe Grade dropdown takes only these, written exactly so.
PIPE_GRADES = ["24,000", "A25", "A", "B", "X42", "X46", "X52", "X56", "X60", "X65", "X70"]
# A grade given as its SMYS (psi) maps through this table. X80 is in the table
# so '80,500 psi' is recognized — and then rejected, because the calculator's
# dropdown does not list X80.
PIPE_GRADE_SMYS = {
    "24,000": 24000, "A25": 25000, "A": 30500, "B": 35500, "X42": 42100,
    "X46": 46400, "X52": 52200, "X56": 56600, "X60": 60200, "X65": 65300,
    "X70": 70300, "X80": 80500,
}

# S457: the calculator's Class Location dropdown takes only 40/50/60/72.
CLASS_LOCATION_VALUES = {1: 72, 2: 60, 3: 50, 4: 40}
CLASS_LOCATION_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}
DEFAULT_CLASS_LOCATION = 1  # class 1 (72) is assumed when nothing says otherwise

# S443: the fabric rule. An anomaly whose type says only corrosion / metal loss
# (External / Internal allowed as qualifiers) -> E-Glass, repair length from the
# Corrosion sheet (C27), no isolation wrap. Anything else — corrosion mixed with
# something else, or no type at all — -> Carbon, repair length from the
# Mechanical sheet (C26), with an isolation wrap under it at C26 + 12 inches.
FABRIC_CORROSION_WORDS = {"corrosion", "metal", "loss"}
FABRIC_QUALIFIER_WORDS = {"external", "internal", "ext", "int"}

# Where each number comes from in the Excel calculator, so the ported formulas
# can be verified cell-for-cell later.
OUTPUT_CELLS = {"Corrosion": "C27", "Mechanical": "C26"}
ISOLATION_EXTRA_INCHES = 12  # S437: Wheel Count Calculator sheet, G2 -> H2


def class_location_number(text) -> int | None:
    """S457: the class (1-4) one Class Location value says ('Class 3', '3',
    'Class III'), or None."""
    raw = " ".join(str(text or "").split()).upper()
    if not raw or raw == "-":
        return None
    digit = re.search(r"\b([1-4])\b", raw)
    if digit:
        return int(digit.group(1))
    roman = re.search(r"\b(IV|III|II|I)\b", raw.replace("CLASS", " "))
    return CLASS_LOCATION_ROMAN[roman.group(1)] if roman else None


def pipe_grade_for_calculator(text) -> tuple[str | None, str]:
    """S456: (grade as the calculator takes it or None, what was read) for one
    Pipe Grade value as written — 'X-52', 'API 5L X52', 'Gr. B', 'B', 'A25',
    '52,200' (its SMYS)... A grade the calculator does not list (X80) gives None."""
    raw = " ".join(str(text or "").split())
    if not raw or raw == "-":
        return None, "no pipe grade given"
    upper = raw.upper().replace("API 5L", " ").replace("API5L", " ")
    found = None
    number = re.search(r"\b(\d{2},?\d{3})\b", upper)
    if number:  # its SMYS in psi (24,000 is a grade of its own)
        psi = int(number.group(1).replace(",", ""))
        found = next((g for g, v in PIPE_GRADE_SMYS.items() if v == psi), None)
        if found is None:
            return None, f"'{raw}' - {psi:,} psi is not an SMYS in the grade table"
    else:
        grade = re.search(r"\bX\s*-?\s*(\d{2})\b", upper)
        if grade:
            found = f"X{grade.group(1)}"
        elif re.search(r"\bA\s*-?\s*25\b", upper):
            found = "A25"
        else:
            letter = re.search(r"(?:\bGR(?:ADE)?\.?\s*|^\s*)([AB])\s*$", upper)
            found = letter.group(1) if letter else None
    if found is None:
        return None, f"'{raw}' - not a pipe grade"
    if found not in PIPE_GRADES:
        return None, f"'{raw}' = {found} - not one the calculator lists"
    return found, f"'{raw}' = {found}"


def fabric_for_type(text) -> tuple[str, str]:
    """S443: ('E-Glass' or 'Carbon', why) for one anomaly type as written."""
    words = set(re.findall(r"[a-z]+", str(text or "").lower())) - FABRIC_QUALIFIER_WORDS
    if not words:
        return "Carbon", "no anomaly type given - Carbon"
    if words <= FABRIC_CORROSION_WORDS:
        return "E-Glass", f"'{text}' is only corrosion / metal loss - E-Glass"
    if words & FABRIC_CORROSION_WORDS:
        return "Carbon", f"'{text}' is corrosion with something else - Carbon"
    return "Carbon", f"'{text}' is not corrosion / metal loss - Carbon"


@dataclass(frozen=True)
class CalculatorInputs:
    """One repair's inputs, exactly the Excel calculator's input surface.
    All measurements in inches; pipe_grade one of PIPE_GRADES; class_location
    the class number 1-4 (not the 72/60/50/40 factor)."""

    outside_diameter: float   # E4
    wall_thickness: float     # E5
    pipe_grade: str           # E7
    class_location: int       # E10 (as class 1-4)
    depth: float              # E13
    length: float             # E15
    width: float              # E16
    anomaly_type: str         # drives the fabric pick (S443)


@dataclass(frozen=True)
class EngineResult:
    """What a formula engine gives back. available=False means NO numbers
    exist — the page must say so and must never show a placeholder as real."""

    available: bool
    repair_length_in: float | None  # Linear Length of Repair (C27 or C26)
    note: str


class FormulaEngine:
    """Where the Linear Length of Repair comes from. Implementations are
    swapped in with set_engine() once the xlsm formulas are ported and
    verified against Excel."""

    name = "formula engine"

    def linear_length_of_repair(self, inputs: CalculatorInputs, sheet: str) -> EngineResult:
        raise NotImplementedError


class PendingExcelEngine(FormulaEngine):
    """The only engine until we have AE_Composite_Calculator.xlsm: it returns
    no numbers, on purpose. Do not replace this with an approximation."""

    name = "pending - waiting on the AE Composite Calculator Excel file"

    def linear_length_of_repair(self, inputs: CalculatorInputs, sheet: str) -> EngineResult:
        return EngineResult(
            available=False,
            repair_length_in=None,
            note=(
                "The repair length formulas live in the AE Composite Calculator "
                "Excel file, which we do not have yet. No numbers are shown "
                "until those formulas are brought in and checked against Excel."
            ),
        )


_engine: FormulaEngine = PendingExcelEngine()


def get_engine() -> FormulaEngine:
    return _engine


def set_engine(engine: FormulaEngine) -> None:
    global _engine
    _engine = engine


@dataclass(frozen=True)
class CalculationResult:
    fabric: str                       # 'E-Glass' or 'Carbon'
    fabric_why: str
    sheet: str                        # 'Corrosion' or 'Mechanical'
    output_cell: str                  # C27 / C26, for the Excel verification later
    design_factor: int                # 72 / 60 / 50 / 40 from the class location
    isolation_wrap: bool              # Carbon repairs get one, E-Glass do not
    results_available: bool
    repair_length_in: float | None
    isolation_wrap_length_in: float | None  # repair length + 12, Carbon only
    engine_note: str


def run_calculator(inputs: CalculatorInputs) -> CalculationResult:
    """The deterministic path: pick the fabric and sheet from the anomaly type
    (S443), then ask the formula engine for the Linear Length of Repair."""
    fabric, why = fabric_for_type(inputs.anomaly_type)
    sheet = "Corrosion" if fabric == "E-Glass" else "Mechanical"
    isolation = fabric == "Carbon"
    engine = get_engine()
    result = engine.linear_length_of_repair(inputs, sheet)
    isolation_length = None
    if isolation and result.available and result.repair_length_in is not None:
        isolation_length = result.repair_length_in + ISOLATION_EXTRA_INCHES
    return CalculationResult(
        fabric=fabric,
        fabric_why=why,
        sheet=sheet,
        output_cell=OUTPUT_CELLS[sheet],
        design_factor=CLASS_LOCATION_VALUES[inputs.class_location],
        isolation_wrap=isolation,
        results_available=result.available,
        repair_length_in=result.repair_length_in if result.available else None,
        isolation_wrap_length_in=isolation_length,
        engine_note=result.note,
    )
