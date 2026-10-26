"""OmegaWrap repair formulas ported from AE_Composite_Calculator (T531).

Source workbook: docs/reference/maxim-handoff/
AE_Composite_Calculator_v2.06_072926_NO_TimeWarp.xlsm. Every function here is a
cell-for-cell port of the 'Corrosion Calculations' and 'Mechanical
Calculations' sheets (ASME PCC-2-2018 Article 401), with the source cell named
next to each line. Verified against the workbook by
scripts/verify_omegawrap_xlsm.py; report in
docs/research/omegawrap-formula-port-T531-verification.md.

Fixed choices the workbook leaves as inputs, pinned to its shipped defaults
because the T525 page does not ask for them:
  - Service Factor = 0.5 (Corrosion!E21 / Mechanical!E20; '1 000-h Data')
  - Application Technique = 'Layer over Layer' (Corrosion!E33 /
    Mechanical!E32) -> wrap overlap 0% ('OW Variables'!M4:N4). The Spiral
    technique path (J62/J60, VBA RepairWrapLengthSpiral) is NOT ported.
  - Units = Imperial (Corrosion!I8 / Mechanical!G5); all math in inches/psi.
  - Install temperature only drives warning flags in the workbook, never a
    number in these chains, so it is not an input here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .omegawrap_calc import (
    PIPE_GRADE_SMYS,
    CalculatorInputs,
    EngineResult,
    FormulaEngine,
)

# 'OW Variables'!B4:D15 columns 2-3 (SMYS lives in omegawrap_calc already).
PIPE_GRADE_UTS = {
    "24,000": 44000, "A25": 45000, "A": 48600, "B": 60200, "X42": 60200,
    "X46": 63100, "X52": 66700, "X56": 71100, "X60": 75400, "X65": 77500,
    "X70": 82700, "X80": 90600,
}

# 'OW Variables'!T4:AB6 — composite material table (TimeWarp row unused: this
# workbook version is "NO TimeWarp" and the fabric rule never picks it).
@dataclass(frozen=True)
class CompositeProperties:
    hoop_modulus_psi: float        # U
    axial_modulus_psi: float       # V
    composite_strain: float        # W
    lt_strength_psi: float         # X
    layer_thickness_in: float      # Y
    resin_coverage_ft2_per_qt: float  # Z
    roll_multiple_ft: float        # AA
    standard_width_in: float       # AB


COMPOSITES = {
    "E-Glass": CompositeProperties(5_700_000, 1_700_000, 0.0025, 32_550, 0.036, 15, 25, 12),
    "Carbon": CompositeProperties(10_990_000, 2_030_000, 0.0025, 56_483, 0.022, 30, 15, 12),
}

STEEL_PIPE_MODULUS_PSI = 30_000_000   # 'OW Variables'!AC4
PUTTY_IN3_PER_PT = 28.875             # 'OW Variables'!S14
SERVICE_FACTOR = 0.5                  # Corrosion!E21 / Mechanical!E20 default
WRAP_OVERLAP_PCT = 0                  # 'OW Variables'!N4 — Layer over Layer

# 'OW Variables'!H22, shown when defect depth >= wall thickness (flag 3).
DEPTH_ERROR = "Defect depth must be less than wall thickness"


def excel_round(x: float, digits: int = 0) -> float:
    """Excel ROUND: half away from zero (Python's round is half-even)."""
    scale = 10 ** digits
    scaled = x * scale
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return rounded / scale


def excel_roundup(x: float, digits: int = 0) -> float:
    """Excel ROUNDUP: away from zero."""
    scale = 10 ** digits
    scaled = x * scale
    rounded = math.ceil(scaled) if scaled >= 0 else math.floor(scaled)
    return rounded / scale


def excel_ceiling(x: float, significance: float) -> float:
    """Excel CEILING / CEILING.MATH for positive significance: the nearest
    multiple of `significance` at or above x. Same IEEE doubles as Excel."""
    return math.ceil(x / significance) * significance


@dataclass(frozen=True)
class RepairOutputs:
    """One sheet's REPAIR OUTPUTS block, field = source cell on that sheet's
    Calculations tab (Corrosion Calculations / Mechanical Calculations)."""

    smys_psi: float                   # J13
    uts_psi: float                    # J14
    maop_psig: float                  # J16
    derated_pressure_psig: float      # J17
    number_of_layers: int             # Corr J55->J38 / Mech J53->J36
    wrap_segments: float              # Corr J56 / Mech J54 ("number of sets")
    repair_thickness_in: float        # Corr J57->J39 / Mech J55->J37
    finished_od_in: float             # Corr J40 / Mech J38
    linear_length_of_repair_in: float  # Corr J52->J42 / Mech J50->J40
    fabric_length_per_set_ft: float   # Corr J59->J41 / Mech J57->J39
    total_fabric_length_ft: float     # Corr J60->J43 / Mech J58->J41
    resin_per_set_qt_per_ft: float    # Corr J69->J44 / Mech RNDUP(J67,2)->J42
    total_resin_qt: float             # Corr J70->J45 / Mech RNDUP(J68,2)->J43
    primer_resin_qt: float            # Corr J68->J46 / Mech J66->J44
    putty_pt: float                   # Corr J74->J47 / Mech J72->J45
    isolation_wrap_per_ft: float      # Corr J71 / Mech J69
    isolation_wrap_total: float       # Corr J72 / Mech J70
    isolation_wrap_resin_qt: float    # Corr J73 / Mech J71
    minimum_layers_applied: bool      # Corr I55 / Mech I53 (flag 2)


def _common(od: float, wt: float, grade: str, pct_smys: float) -> tuple[float, float, float, float]:
    smys = PIPE_GRADE_SMYS[grade]                     # J13: VLOOKUP grade
    uts = PIPE_GRADE_UTS[grade]                       # J14: VLOOKUP grade
    maop_fraction = pct_smys / 100 if pct_smys > 1 else pct_smys  # J15
    maop = excel_round(maop_fraction * ((2 * (smys * wt)) / od), 0)  # J16
    derated = excel_round(0.8 * maop, 0)              # J17
    return smys, uts, maop, derated


def _shared_tail(od, length, layers, layer_t, layer_width, resin_coverage,
                 llr, primer_gap_in):
    """The output tail both sheets share (identical formulas, different cells):
    finished OD, segments, thickness, fabric lengths, resin, primer,
    isolation wrap. `primer_gap_in` is the only constant that differs
    (Corrosion J68 uses OD+0.06, Mechanical J66 uses OD+0.025)."""
    finished_od = ((layers * 2) * layer_t) + od                  # J40 / J38
    # J56 / J54: (1/(1-overlap%/100)) * (LLR/width - 1) + 1; overlap is 0
    segments = (1 / (1 - WRAP_OVERLAP_PCT / 100)) * (llr / layer_width - 1) + 1
    thickness = excel_ceiling(layers * layer_t, layer_t)         # J57 / J55
    # J59 / J57: annulus area between finished OD and pipe OD, as fabric feet
    fabric_per_set = excel_roundup(
        (math.pi * (((finished_od ** 2) / 4) - ((od ** 2) / 4)) / layer_t) / 12, 3
    )
    total_fabric = fabric_per_set * segments                     # J60 / J58
    total_area = total_fabric                                    # J67 / J65
    total_resin = total_area / resin_coverage                    # J70 / J68
    resin_per_set = total_resin / segments                       # J69 / J67
    primer = ((math.pi * (od + primer_gap_in)) - (math.pi * od)) * (llr + 4) / 57.75  # J68 / J66
    iw_per_ft = ((od * math.pi) * 2) / 12                        # J71 / J69
    iw_total = iw_per_ft * ((llr / 12) + 1)                      # J72 / J70
    iw_resin = excel_roundup(iw_total * 0.06, 0)                 # J73 / J71
    return (finished_od, segments, thickness, fabric_per_set, total_fabric,
            total_resin, resin_per_set, primer, iw_per_ft, iw_total, iw_resin)


def corrosion_outputs(od: float, wt: float, grade: str, pct_smys: float,
                      depth: float, length: float, width: float,
                      composite: str = "E-Glass") -> RepairOutputs:
    """'Corrosion Calculations' sheet. Raises ValueError with the workbook's
    own message when depth >= wall thickness (error flag I20 -> outputs '-')."""
    if depth >= wt:  # I20: =IF(J20>=J11, flag 3, "")
        raise ValueError(DEPTH_ERROR)
    smys, uts, maop, derated = _common(od, wt, grade, pct_smys)
    props = COMPOSITES[composite]
    remaining_wall = excel_round(wt - depth, 5)                  # J24
    # J50 (PCC-2 Eq 12): design laminate thickness for hoop stress
    t_repair = ((maop * od / 2) - (remaining_wall * smys)) * (
        1 / (SERVICE_FACTOR * props.lt_strength_psi)
    )
    l_over = 2.5 * math.sqrt(od * wt / 2)                        # J51 (Eq 17)
    llr = excel_ceiling(2 * l_over + length, 12)                 # J52 (Eq 19)
    # J55: layers, minimum 3
    raw_layers = excel_roundup(t_repair / props.layer_thickness_in + 0.5, 0)
    layers = 3 if raw_layers < 3 else int(raw_layers)
    (finished_od, segments, thickness, fabric_per_set, total_fabric,
     total_resin, resin_per_set, primer, iw_per_ft, iw_total,
     iw_resin) = _shared_tail(od, length, layers, props.layer_thickness_in,
                              props.standard_width_in,
                              props.resin_coverage_ft2_per_qt, llr,
                              primer_gap_in=0.06)
    putty = (depth * length * width) / PUTTY_IN3_PER_PT          # J74
    return RepairOutputs(
        smys_psi=smys, uts_psi=uts, maop_psig=maop, derated_pressure_psig=derated,
        number_of_layers=layers, wrap_segments=segments,
        repair_thickness_in=thickness, finished_od_in=finished_od,
        linear_length_of_repair_in=llr, fabric_length_per_set_ft=fabric_per_set,
        total_fabric_length_ft=total_fabric, resin_per_set_qt_per_ft=resin_per_set,
        total_resin_qt=total_resin, primer_resin_qt=primer, putty_pt=putty,
        isolation_wrap_per_ft=iw_per_ft, isolation_wrap_total=iw_total,
        isolation_wrap_resin_qt=iw_resin,
        minimum_layers_applied=raw_layers < 3,
    )


def mechanical_outputs(od: float, wt: float, grade: str, pct_smys: float,
                       depth: float, length: float, width: float,
                       composite: str = "Carbon") -> RepairOutputs:
    """'Mechanical Calculations' sheet. The workbook has NO depth-vs-wall gate
    on this sheet (its I20 is empty), so none is added here; layer count
    depends on wall thickness only (J53 = 1.5*WT / layer thickness)."""
    smys, uts, maop, derated = _common(od, wt, grade, pct_smys)
    props = COMPOSITES[composite]
    l_over = 2.5 * math.sqrt(od * wt / 2)                        # J49 (Eq 17)
    llr = excel_ceiling(2 * l_over + length, 12)                 # J50 (Eq 19)
    # J53: layers from 1.5x wall thickness, minimum 3
    raw_layers = excel_roundup(wt * 1.5 / props.layer_thickness_in, 0)
    layers = 3 if raw_layers < 3 else int(raw_layers)
    (finished_od, segments, thickness, fabric_per_set, total_fabric,
     total_resin, resin_per_set, primer, iw_per_ft, iw_total,
     iw_resin) = _shared_tail(od, length, layers, props.layer_thickness_in,
                              props.standard_width_in,
                              props.resin_coverage_ft2_per_qt, llr,
                              primer_gap_in=0.025)
    putty = (depth * length * width) / PUTTY_IN3_PER_PT          # J72
    return RepairOutputs(
        smys_psi=smys, uts_psi=uts, maop_psig=maop, derated_pressure_psig=derated,
        number_of_layers=layers, wrap_segments=segments,
        repair_thickness_in=thickness, finished_od_in=finished_od,
        linear_length_of_repair_in=llr, fabric_length_per_set_ft=fabric_per_set,
        total_fabric_length_ft=total_fabric,
        # Mechanical sheet rounds these two up to 2 decimals (J42/J43);
        # Corrosion shows them raw. Kept faithful to each sheet.
        resin_per_set_qt_per_ft=excel_roundup(resin_per_set, 2),
        total_resin_qt=excel_roundup(total_resin, 2),
        primer_resin_qt=primer, putty_pt=putty,
        isolation_wrap_per_ft=iw_per_ft, isolation_wrap_total=iw_total,
        isolation_wrap_resin_qt=iw_resin,
        minimum_layers_applied=raw_layers < 3,
    )


class PCC2FormulaEngine(FormulaEngine):
    """The ported engine. Sheet choice follows the app's fabric rule exactly:
    Corrosion sheet runs with E-Glass, Mechanical sheet with Carbon."""

    name = "AE Composite Calculator v2.06 formulas (ASME PCC-2-2018)"

    def full_outputs(self, inputs: CalculatorInputs, sheet: str) -> RepairOutputs:
        from .omegawrap_calc import CLASS_LOCATION_VALUES
        pct_smys = CLASS_LOCATION_VALUES[inputs.class_location]
        fn = corrosion_outputs if sheet == "Corrosion" else mechanical_outputs
        composite = "E-Glass" if sheet == "Corrosion" else "Carbon"
        return fn(
            inputs.outside_diameter, inputs.wall_thickness, inputs.pipe_grade,
            pct_smys, inputs.depth, inputs.length, inputs.width,
            composite=composite,
        )

    def linear_length_of_repair(self, inputs: CalculatorInputs, sheet: str) -> EngineResult:
        try:
            outputs = self.full_outputs(inputs, sheet)
        except ValueError as err:
            return EngineResult(available=False, repair_length_in=None, note=str(err))
        return EngineResult(
            available=True,
            repair_length_in=outputs.linear_length_of_repair_in,
            note=self.name,
            details=outputs,
        )
