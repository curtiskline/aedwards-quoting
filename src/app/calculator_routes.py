"""OmegaWrap calculator page (T525 form, T531 formulas).

One form, the Excel calculator's exact input surface, plain labels. The
repair numbers come from the PCC-2 engine ported from the AE Composite
Calculator workbook and verified against it (see omegawrap_formulas).
"""

from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import login_required

from .omegawrap_calc import (
    CLASS_LOCATION_VALUES,
    PIPE_GRADES,
    CalculatorInputs,
    class_location_number,
    pipe_grade_for_calculator,
    run_calculator,
)

calculator_bp = Blueprint("calculator", __name__, url_prefix="/calculator")


MEASUREMENTS = [
    # (form field, label shown on the page)
    ("outside_diameter", "Outside diameter (inches)"),
    ("wall_thickness", "Wall thickness (inches)"),
    ("depth", "Depth of the anomaly (inches)"),
    ("length", "Length of the anomaly (inches)"),
    ("width", "Width of the anomaly (inches)"),
]


def _parse_measurements(form) -> tuple[dict[str, float], list[str]]:
    values: dict[str, float] = {}
    errors: list[str] = []
    for field, label in MEASUREMENTS:
        raw = (form.get(field) or "").strip()
        if not raw:
            errors.append(f"{label} is required.")
            continue
        try:
            number = float(raw.replace(",", ""))
        except ValueError:
            errors.append(f"{label} must be a number. '{raw}' is not.")
            continue
        if number <= 0:
            errors.append(f"{label} must be more than 0.")
            continue
        values[field] = number
    return values, errors


def _parse_pipe_grade(form) -> tuple[str | None, str, list[str]]:
    """The grade from the dropdown, or from a typed SMYS (psi) when the grade
    is not known. Both given and disagreeing is an error, not a silent pick."""
    picked = (form.get("pipe_grade") or "").strip()
    smys_text = (form.get("pipe_grade_smys") or "").strip()
    if picked and picked not in PIPE_GRADES:
        return None, "", [f"Pipe grade '{picked}' is not one the calculator takes."]
    if smys_text:
        from_smys, why = pipe_grade_for_calculator(smys_text)
        if from_smys is None:
            return None, "", [f"SMYS could not be read: {why}."]
        if picked and picked != from_smys:
            return None, "", [
                f"The pipe grade dropdown says {picked} but the SMYS says "
                f"{from_smys} ({why}). Pick one or make them match."
            ]
        return from_smys, why, []
    if picked:
        return picked, f"picked from the dropdown: {picked}", []
    return None, "", ["Pick a pipe grade, or type the SMYS in psi."]


def _parse_class_location(form) -> tuple[int, list[str]]:
    raw = (form.get("class_location") or "").strip()
    if not raw:
        return 1, []  # class 1 (72) is assumed when nothing says otherwise
    number = class_location_number(raw)
    if number is None:
        return 1, [f"Class location '{raw}' is not one of class 1, 2, 3 or 4."]
    return number, []


@calculator_bp.get("")
@login_required
def page() -> str:
    return _render(form=None, result=None, errors=[])


@calculator_bp.post("")
@login_required
def calculate() -> str:
    form = request.form
    measurements, errors = _parse_measurements(form)
    grade, grade_why, grade_errors = _parse_pipe_grade(form)
    class_number, class_errors = _parse_class_location(form)
    errors += grade_errors + class_errors
    anomaly_type = (form.get("anomaly_type") or "").strip()

    if errors:
        return _render(form=form, result=None, errors=errors)

    inputs = CalculatorInputs(
        outside_diameter=measurements["outside_diameter"],
        wall_thickness=measurements["wall_thickness"],
        pipe_grade=grade,
        class_location=class_number,
        depth=measurements["depth"],
        length=measurements["length"],
        width=measurements["width"],
        anomaly_type=anomaly_type,
    )
    result = run_calculator(inputs)
    return _render(
        form=form,
        result=result,
        errors=[],
        inputs=inputs,
        grade_why=grade_why,
        class_number=class_number,
    )


def _render(*, form, result, errors, inputs=None, grade_why="", class_number=1) -> str:
    return render_template(
        "calculator/omegawrap.html",
        form=form or {},
        result=result,
        errors=errors,
        inputs=inputs,
        grade_why=grade_why,
        class_number=class_number,
        pipe_grades=PIPE_GRADES,
        class_locations=CLASS_LOCATION_VALUES,
        measurements=MEASUREMENTS,
    )
