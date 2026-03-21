import pytest

from utils import resolve_output_variables
from defaults import COORD_VARS_2D, COORD_VARS_3D


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
