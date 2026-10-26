"""OmegaWrap calculator, simple version (T525): deterministic logic ported from
Maxim's Composite Code Reader, the pluggable formula engine, and the page."""

from __future__ import annotations

import pytest

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import User
from app.omegawrap_calc import (
    CLASS_LOCATION_VALUES,
    PIPE_GRADES,
    CalculatorInputs,
    EngineResult,
    FormulaEngine,
    PendingExcelEngine,
    class_location_number,
    fabric_for_type,
    get_engine,
    pipe_grade_for_calculator,
    run_calculator,
    set_engine,
)


# ---------- pipe grade (S456) ----------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("X52", "X52"),
        ("X-52", "X52"),
        ("API 5L X52", "X52"),
        ("Gr. B", "B"),
        ("B", "B"),
        ("A25", "A25"),
        ("A-25", "A25"),
        ("A", "A"),
        ("52,200", "X52"),
        ("52200", "X52"),
        ("24,000", "24,000"),
        ("X70", "X70"),
    ],
)
def test_pipe_grade_recognized(text, expected):
    grade, _why = pipe_grade_for_calculator(text)
    assert grade == expected


@pytest.mark.parametrize(
    "text",
    [
        "",             # nothing given
        "X80",          # the calculator's dropdown does not list X80
        "80,500",       # X80 via SMYS - recognized, then rejected
        "99,999",       # not an SMYS in the table
        "stainless",    # not a grade at all
    ],
)
def test_pipe_grade_rejected(text):
    grade, why = pipe_grade_for_calculator(text)
    assert grade is None
    assert why  # always says what was read


def test_every_dropdown_grade_passes_through():
    for grade in PIPE_GRADES:
        assert pipe_grade_for_calculator(grade)[0] == grade


# ---------- class location (S457) ----------

@pytest.mark.parametrize(
    "text,expected",
    [("Class 3", 3), ("3", 3), ("Class III", 3), ("class IV", 4), ("1", 1), ("", None), ("-", None), ("Class 7", None)],
)
def test_class_location_number(text, expected):
    assert class_location_number(text) == expected


def test_class_location_factors():
    assert CLASS_LOCATION_VALUES == {1: 72, 2: 60, 3: 50, 4: 40}


# ---------- fabric rule (S443) ----------

@pytest.mark.parametrize(
    "text,fabric",
    [
        ("External Corrosion", "E-Glass"),
        ("corrosion", "E-Glass"),
        ("Internal Metal Loss", "E-Glass"),
        ("Metal Loss", "E-Glass"),
        ("Ext metal loss", "E-Glass"),
        ("Dent", "Carbon"),
        ("Gouge", "Carbon"),
        ("Dent with Metal Loss", "Carbon"),   # corrosion mixed with something else
        ("", "Carbon"),                       # no type given
        ("External", "Carbon"),               # a qualifier alone says nothing
    ],
)
def test_fabric_for_type(text, fabric):
    assert fabric_for_type(text)[0] == fabric


# ---------- run_calculator with the ported engine (T531) ----------

def _inputs(anomaly_type="External Corrosion", class_location=1):
    return CalculatorInputs(
        outside_diameter=20.0,
        wall_thickness=0.375,
        pipe_grade="X52",
        class_location=class_location,
        depth=0.093,
        length=6.0,
        width=3.0,
        anomaly_type=anomaly_type,
    )


def test_corrosion_repair_is_eglass_no_isolation():
    result = run_calculator(_inputs("External Corrosion"))
    assert result.fabric == "E-Glass"
    assert result.sheet == "Corrosion"
    assert result.output_cell == "C27"
    assert result.isolation_wrap is False
    assert result.results_available is True
    # CEILING(2 * 2.5*SQRT(20*0.375/2) + 6, 12) — Corrosion Calculations J52
    assert result.repair_length_in == 24
    assert result.isolation_wrap_length_in is None  # E-Glass never gets one


def test_dent_repair_is_carbon_with_isolation_wrap():
    result = run_calculator(_inputs("Dent", class_location=3))
    assert result.fabric == "Carbon"
    assert result.sheet == "Mechanical"
    assert result.output_cell == "C26"
    assert result.isolation_wrap is True
    assert result.design_factor == 50
    assert result.results_available is True
    assert result.repair_length_in == 24
    assert result.isolation_wrap_length_in == 36  # repair + 12


def test_pluggable_engine_supplies_lengths():
    class FakeEngine(FormulaEngine):
        name = "fake"

        def linear_length_of_repair(self, inputs, sheet):
            return EngineResult(available=True, repair_length_in=18.0, note="fake")

    original = get_engine()
    try:
        set_engine(FakeEngine())
        carbon = run_calculator(_inputs("Dent"))
        assert carbon.results_available is True
        assert carbon.repair_length_in == 18.0
        assert carbon.isolation_wrap_length_in == 30.0  # repair + 12
        eglass = run_calculator(_inputs("External Corrosion"))
        assert eglass.isolation_wrap_length_in is None  # E-Glass never gets one
    finally:
        set_engine(original)


def test_default_engine_is_the_ported_pcc2_engine():
    from app.omegawrap_formulas import PCC2FormulaEngine

    set_engine(None)  # force the lazy default to re-resolve
    try:
        assert isinstance(get_engine(), PCC2FormulaEngine)
    finally:
        set_engine(None)


def test_pending_stub_still_refuses_numbers():
    original = get_engine()
    try:
        set_engine(PendingExcelEngine())
        result = run_calculator(_inputs("Dent"))
        assert result.results_available is False
        assert result.repair_length_in is None
    finally:
        set_engine(original)


# ---------- the page ----------

@pytest.fixture()
def app(db_url, monkeypatch):
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "WTF_CSRF_ENABLED", False, raising=False)
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        user = User(email="owner@example.com", name="Owner", password_hash="")
        user.set_password("secret123")
        db.session.add(user)
        db.session.commit()
        yield app
        db.session.remove()


@pytest.fixture()
def client(app):
    client = app.test_client()
    client.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    return client


VALID_FORM = {
    "outside_diameter": "20",
    "wall_thickness": "0.375",
    "depth": "0.093",
    "length": "6",
    "width": "3",
    "pipe_grade": "X52",
    "pipe_grade_smys": "",
    "class_location": "1",
    "anomaly_type": "External Corrosion",
}


def _csrf_token(client):
    page = client.get("/calculator").get_data(as_text=True)
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return match.group(1) if match else ""


def _post(client, **overrides):
    data = {**VALID_FORM, **overrides, "csrf_token": _csrf_token(client)}
    return client.post("/calculator", data=data)


def test_page_requires_login():
    app = create_app()
    response = app.test_client().get("/calculator")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_page_shows_form_without_pending_banner(client):
    response = client.get("/calculator")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "OmegaWrap calculator" in html
    # T531: the formulas are in and verified; the waiting banner is gone.
    assert "not ready yet" not in html
    for grade in PIPE_GRADES:
        assert f">{grade}</option>" in html


def test_corrosion_submit_shows_eglass_and_numbers(client):
    html = _post(client).get_data(as_text=True)
    assert "E-Glass" in html
    assert "cell C27" in html
    assert "24 inches" in html  # repair length for the VALID_FORM inputs
    assert "Number of layers" in html
    assert "waiting" not in html


def test_dent_submit_shows_carbon_and_isolation(client):
    html = _post(client, anomaly_type="Dent", class_location="3").get_data(as_text=True)
    assert "Carbon" in html
    assert "cell C26" in html
    assert "isolation wrap under it, 12 inches longer" in html
    assert "Design factor 50" in html
    assert "36 inches" in html  # isolation wrap = repair 24 + 12


def test_depth_at_wall_shows_error_not_numbers(client):
    html = _post(client, depth="0.375").get_data(as_text=True)
    assert "No numbers for this one" in html
    assert "Defect depth must be less than wall thickness" in html
    assert "Number of layers" not in html


def test_smys_maps_to_grade(client):
    html = _post(client, pipe_grade="", pipe_grade_smys="52,200").get_data(as_text=True)
    assert "X52" in html


def test_smys_and_dropdown_disagree_is_an_error(client):
    html = _post(client, pipe_grade="X60", pipe_grade_smys="52,200").get_data(as_text=True)
    assert "Pick one or make them match" in html
    assert "What to use" not in html


def test_missing_grade_is_an_error(client):
    html = _post(client, pipe_grade="", pipe_grade_smys="").get_data(as_text=True)
    assert "Pick a pipe grade, or type the SMYS in psi." in html


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("outside_diameter", "", "Outside diameter (inches) is required."),
        ("wall_thickness", "abc", "must be a number"),
        ("length", "0", "must be more than 0"),
        ("depth", "-1", "must be more than 0"),
    ],
)
def test_bad_measurements_are_errors(client, field, value, message):
    html = _post(client, **{field: value}).get_data(as_text=True)
    assert message in html
    assert "What to use" not in html


def test_unknown_anomaly_type_defaults_to_carbon(client):
    html = _post(client, anomaly_type="").get_data(as_text=True)
    assert "Carbon" in html
    assert "cell C26" in html
