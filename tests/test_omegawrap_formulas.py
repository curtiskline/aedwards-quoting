"""T531: the PCC-2 engine ported from AE_Composite_Calculator_v2.06.

The anchor tests pin the engine to the workbook's own cached values
(openpyxl data_only=True) at the inputs the file shipped with. Those numbers
were read straight out of the xlsm, not computed here. The wider input grid
is covered by scripts/verify_omegawrap_xlsm.py, which evaluates the
workbook's formulas independently with the `formulas` library.
"""

import pytest

from src.app.omegawrap_calc import CalculatorInputs, run_calculator, set_engine
from src.app.omegawrap_formulas import (
    DEPTH_ERROR,
    PCC2FormulaEngine,
    corrosion_outputs,
    mechanical_outputs,
)

approx = lambda x: pytest.approx(x, rel=1e-12, abs=1e-12)


class TestCorrosionAnchor:
    """Corrosion sheet as shipped: OD 20, WT 0.25, X70, 40 %SMYS,
    depth 0.21, length 52.81, width 6, composite Carbon."""

    @pytest.fixture(scope="class")
    def out(self):
        return corrosion_outputs(20, 0.25, "X70", 40, 0.21, 52.81, 6,
                                 composite="Carbon")

    def test_maop(self, out):          # J16 / J17
        assert out.maop_psig == 703
        assert out.derated_pressure_psig == 562

    def test_layers(self, out):        # J38 (via J55)
        assert out.number_of_layers == 8
        assert not out.minimum_layers_applied

    def test_linear_length(self, out):  # J42 (via J52)
        assert out.linear_length_of_repair_in == 72

    def test_geometry(self, out):      # J40, J56, J57
        assert out.finished_od_in == approx(20.352)
        assert out.wrap_segments == approx(6)
        assert out.repair_thickness_in == approx(0.176)

    def test_fabric(self, out):        # J59, J60
        assert out.fabric_length_per_set_ft == approx(42.257)
        assert out.total_fabric_length_ft == approx(253.54199999999997)

    def test_resin_primer_putty(self, out):  # J69, J70, J68, J74
        assert out.resin_per_set_qt_per_ft == approx(1.4085666666666665)
        assert out.total_resin_qt == approx(8.4514)
        assert out.primer_resin_qt == approx(0.24806341991981135)
        assert out.putty_pt == approx(2.3044363636363636)

    def test_isolation_wrap(self, out):  # J71, J72, J73
        assert out.isolation_wrap_per_ft == approx(10.471975511965978)
        assert out.isolation_wrap_total == approx(73.30382858376184)
        assert out.isolation_wrap_resin_qt == 5


class TestMechanicalAnchor:
    """Mechanical sheet as shipped: OD 30, WT 0.375, X52, 72 %SMYS,
    depth 0.19, length 12, width 5, composite Carbon."""

    @pytest.fixture(scope="class")
    def out(self):
        return mechanical_outputs(30, 0.375, "X52", 72, 0.19, 12, 5)

    def test_maop(self, out):          # J16
        assert out.maop_psig == 940

    def test_layers(self, out):        # J36 (via J53)
        assert out.number_of_layers == 26

    def test_linear_length(self, out):  # J40 (via J50)
        assert out.linear_length_of_repair_in == 24

    def test_geometry(self, out):      # J38, J54, J55
        assert out.finished_od_in == approx(31.144)
        assert out.wrap_segments == approx(2)
        assert out.repair_thickness_in == approx(0.572)

    def test_fabric(self, out):        # J39, J41
        assert out.fabric_length_per_set_ft == approx(208.098)
        assert out.total_fabric_length_ft == approx(416.196)

    def test_resin_rounded_up_two_decimals(self, out):  # J42, J43
        assert out.resin_per_set_qt_per_ft == approx(6.94)
        assert out.total_resin_qt == approx(13.88)

    def test_primer_putty(self, out):  # J44 (J66), J45 (J72)
        assert out.primer_resin_qt == approx(0.03807991095260205)
        assert out.putty_pt == approx(0.3948051948051949)

    def test_isolation_wrap(self, out):  # J69, J70, J71
        assert out.isolation_wrap_per_ft == approx(15.707963267948964)
        assert out.isolation_wrap_total == approx(47.12388980384689)
        assert out.isolation_wrap_resin_qt == 3


class TestGates:
    def test_corrosion_depth_at_wall_is_an_error(self):
        # Corrosion Calculations I20 flags depth >= wall; outputs become '-'.
        with pytest.raises(ValueError, match=DEPTH_ERROR):
            corrosion_outputs(20, 0.25, "X70", 40, 0.25, 52.81, 6)

    def test_mechanical_has_no_depth_gate_like_the_workbook(self):
        # Mechanical Calculations I20 is empty in the xlsm: no gate ported.
        out = mechanical_outputs(30, 0.375, "X52", 72, 0.375, 12, 5)
        assert out.linear_length_of_repair_in == 24

    def test_minimum_three_layers(self):
        # Thin repair: raw layer count under 3 is clamped to 3 (Corr J55).
        out = corrosion_outputs(4.5, 0.5, "X70", 40, 0.01, 6, 2)
        assert out.number_of_layers == 3
        assert out.minimum_layers_applied


class TestEngineIntegration:
    @pytest.fixture(autouse=True)
    def engine(self):
        set_engine(PCC2FormulaEngine())
        yield

    def test_eglass_corrosion_path(self):
        inputs = CalculatorInputs(20, 0.25, "X70", 1, 0.1, 52.81, 6,
                                  "External Corrosion")
        result = run_calculator(inputs)
        assert result.fabric == "E-Glass"
        assert result.sheet == "Corrosion"
        assert result.results_available
        assert result.repair_length_in == 72       # length chain is depth-free
        assert result.isolation_wrap_length_in is None
        # E-Glass layer thickness 0.036 drives the corrosion layer count
        assert result.details.number_of_layers >= 3

    def test_carbon_mechanical_path_isolation_plus_12(self):
        inputs = CalculatorInputs(30, 0.375, "X52", 1, 0.19, 12, 5, "Dent")
        result = run_calculator(inputs)
        assert result.fabric == "Carbon"
        assert result.sheet == "Mechanical"
        assert result.repair_length_in == 24
        assert result.isolation_wrap_length_in == 36   # Wheel Count G2+12
        assert result.details.number_of_layers == 26

    def test_depth_error_shows_no_numbers(self):
        inputs = CalculatorInputs(20, 0.25, "X70", 1, 0.30, 52.81, 6,
                                  "External Corrosion")
        result = run_calculator(inputs)
        assert not result.results_available
        assert result.repair_length_in is None
        assert DEPTH_ERROR in result.engine_note
