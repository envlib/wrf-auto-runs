import pytest

from utils import resolve_output_variables
from defaults import COORD_VARS_2D, COORD_VARS_3D, OUTPUT_PRESETS


class TestResolveOutputVariables:
    def test_2d_only_adds_coords_no_3d_aux(self):
        result = resolve_output_variables(['T2', 'Q2'])
        result_set = set(result)
        assert COORD_VARS_2D <= result_set
        assert not (COORD_VARS_3D & result_set)
        assert 'T2' in result_set
        assert 'Q2' in result_set

    def test_3d_present_adds_both_coord_sets(self):
        result = resolve_output_variables(['T2', 'T'])
        result_set = set(result)
        assert COORD_VARS_2D <= result_set
        assert COORD_VARS_3D <= result_set
        assert 'T2' in result_set
        assert 'T' in result_set

    def test_only_3d_vars(self):
        result = resolve_output_variables(['U', 'V', 'QVAPOR'])
        result_set = set(result)
        assert COORD_VARS_2D <= result_set
        assert COORD_VARS_3D <= result_set
        assert {'U', 'V', 'QVAPOR'} <= result_set

    def test_user_already_lists_coords_no_duplicates(self):
        result = resolve_output_variables(['XLAT', 'T2'])
        assert len(result) == len(set(result))
        assert 'XLAT' in result
        assert 'T2' in result

    def test_moisture_species_triggers_3d(self):
        result = resolve_output_variables(['QCLOUD'])
        result_set = set(result)
        assert COORD_VARS_3D <= result_set

    def test_result_is_sorted(self):
        result = resolve_output_variables(['Z2', 'A2', 'T'])
        assert result == sorted(result)


class TestOutputPresets:
    def test_wrf_to_int_preset_exists(self):
        assert 'wrf_to_int' in OUTPUT_PRESETS

    def test_wrf_to_int_contains_required_3d_vars(self):
        preset = OUTPUT_PRESETS['wrf_to_int']
        assert {'T', 'U', 'V', 'P', 'PB', 'PH', 'PHB', 'QVAPOR'} <= preset

    def test_wrf_to_int_contains_required_surface_vars(self):
        preset = OUTPUT_PRESETS['wrf_to_int']
        assert {'PSFC', 'T2', 'HGT', 'TSK', 'U10', 'V10', 'Q2', 'XLAND'} <= preset

    def test_wrf_to_int_contains_soil_vars(self):
        preset = OUTPUT_PRESETS['wrf_to_int']
        assert {'DZS', 'SMOIS', 'TSLB'} <= preset

    def test_wrf_to_int_triggers_3d_coord_vars(self):
        """Preset has 3D vars, so resolve should auto-add 3D coord vars."""
        preset = OUTPUT_PRESETS['wrf_to_int']
        result = resolve_output_variables(list(preset))
        result_set = set(result)
        assert COORD_VARS_2D <= result_set
        assert COORD_VARS_3D <= result_set

    def test_preset_merged_with_user_vars(self):
        """Simulates what params.py does: preset vars + user vars merged."""
        preset = OUTPUT_PRESETS['wrf_to_int']
        user_vars = {'SWDOWN', 'GLW'}
        combined = sorted(preset | user_vars)
        result = resolve_output_variables(combined)
        result_set = set(result)
        assert 'SWDOWN' in result_set
        assert 'GLW' in result_set
        assert 'T' in result_set  # from preset

    def test_all_preset_values_are_strings(self):
        for name, preset in OUTPUT_PRESETS.items():
            assert isinstance(preset, set), f"Preset {name} should be a set"
            assert all(isinstance(v, str) for v in preset), f"Preset {name} has non-string values"


class TestWvtTracerExpansion:
    def test_default_single_region_no_expansion(self):
        result = set(resolve_output_variables(['qv_tr', 'TR_RAINNC']))  # n_wvt_regions defaults to 1
        assert 'qv_tr' in result
        assert 'qv_tr_02' not in result

    def test_species_expanded_to_all_regions(self):
        result = set(resolve_output_variables(['qv_tr'], n_wvt_regions=3))
        assert {'qv_tr', 'qv_tr_02', 'qv_tr_03'} <= result
        assert 'qv_tr_04' not in result

    def test_all_six_species_expand(self):
        species = ['qv_tr', 'qc_tr', 'qr_tr', 'qi_tr', 'qs_tr', 'qg_tr']
        result = set(resolve_output_variables(species, n_wvt_regions=2))
        for sp in species:
            assert {sp, f'{sp}_02'} <= result

    def test_tr_thum_expanded_preserving_case(self):
        result = set(resolve_output_variables(['TR_THUM_U_PHY_DT'], n_wvt_regions=2))
        assert {'TR_THUM_U_PHY_DT', 'TR_THUM_U_PHY_DT_02'} <= result

    def test_region_dimensioned_field_not_expanded(self):
        # TR_RAINNC carries a region axis on one variable -> no per-region members.
        result = set(resolve_output_variables(['TR_RAINNC'], n_wvt_regions=3))
        assert 'TR_RAINNC' in result
        assert 'TR_RAINNC_02' not in result

    def test_tracer_member_triggers_3d_coords(self):
        result = set(resolve_output_variables(['qv_tr'], n_wvt_regions=2))
        assert COORD_VARS_3D <= result

    def test_already_suffixed_member_not_double_expanded(self):
        result = set(resolve_output_variables(['qv_tr_02'], n_wvt_regions=3))
        assert 'qv_tr_02' in result
        assert 'qv_tr_03' not in result  # a suffixed member is not a base name
