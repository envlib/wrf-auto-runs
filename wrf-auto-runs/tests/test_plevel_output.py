"""
Pressure-level output (WRF p_lev_diags, stream auxhist23, wrfplevels_d0N files) and the per-file
`output_variables` that replaced the top-level list.

Written negative-first (plan round plev-autoruns-plan-1): every refusal has a must-fail case, and the
filter is exercised through monitor_wrf.deliver_output_files -- the seam production uses -- because a
helper-level test let the old `if params.output_variables:` gate survive mutation.
"""
import datetime
import os
import shutil
import subprocess

import f90nml
import h5netcdf
import numpy as np
import pytest

import defaults
import params
import utils
from set_params import set_nml_params

# The block the C1 configs would carry if decision 1 is yes (docs/storm_detection_survey.md in
# wrf-model-eval). No production config has it yet, so this is the closest real `ok_` control.
C1_BLOCK = {
    'output': True,
    'p_levels_hpa': [900, 850, 600, 500, 300],
    'output_variables': ['U_PL', 'V_PL', 'T_PL', 'Q_PL', 'GHT_PL'],
}
KEPT_C1 = {'P_PL', 'U_PL', 'V_PL', 'T_PL', 'Q_PL', 'GHT_PL', 'Times', 'XLAT', 'XLONG'}


def _cfg(block=None, **extra):
    """A minimal config dict for the pure resolvers."""
    cfg = {'time_control': {'history_file': {'interval_hours': [3], 'begin_hours': 0}},
           'domains': {'p_top_requested': 5000}}
    if block is not None:
        cfg['time_control']['p_level_file'] = block
    cfg.update(extra)
    return cfg


@pytest.fixture()
def no_sentry(monkeypatch):
    monkeypatch.setattr(params, 'is_sentry', False)


# ------------------------------------------------------------------------------- namelist


class TestNamelist:

    def test_block_absent_is_off_and_writes_no_stream(self, mock_params, tmp_path):
        _, _, _, output_files = set_nml_params()
        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['diags']['p_lev_diags'] == 0
        assert not any(k.startswith('auxhist23') for k in wrf['time_control'])
        assert not any(f.startswith('wrfplevels') for f in output_files)

    def test_block_on_writes_levels_in_pa_and_the_stream(self, mock_params, tmp_path):
        mock_params['time_control']['p_level_file'] = dict(C1_BLOCK)
        _, _, _, output_files = set_nml_params()
        wrf = f90nml.read(tmp_path / 'namelist.input')
        d, tc = wrf['diags'], wrf['time_control']
        assert d['p_lev_diags'] == 1
        assert d['press_levels'] == [90000.0, 85000.0, 60000.0, 50000.0, 30000.0]
        assert d['num_press_levels'] == 5
        # written explicitly even at their defaults: the files do not record them
        assert d['extrap_below_grnd'] == 1 and d['use_tot_or_hyd_p'] == 2 and d['p_lev_missing'] == -999.0
        assert tc['auxhist23_outname'] == 'wrfplevels_d<domain>_<date>.nc'
        assert tc['io_form_auxhist23'] == 2
        assert tc['auxhist23_interval'] == tc['history_interval'] == [60, 60, 60]
        assert tc['frames_per_auxhist23'] == tc['frames_per_outfile'] == [24, 24, 24]
        assert 'wrfplevels' in {f.split('_d')[0] for f in output_files}

    def test_begin_mirrors_history_begin_not_the_summary_offset(self, mock_params, tmp_path):
        """C1 chunk 1 carries the whole 672 h spin-up as history_begin; the stream must follow it."""
        mock_params['time_control']['history_file']['begin_hours'] = 24
        mock_params['time_control']['p_level_file'] = dict(C1_BLOCK)
        mock_params['time_control']['z_level_file'] = {'output': True, 'z_levels': [100, 500]}
        set_nml_params()
        tc = f90nml.read(tmp_path / 'namelist.input')['time_control']
        assert tc['history_begin'] == [1440, 1440, 1440]
        assert tc['auxhist23_begin'] == tc['history_begin']
        assert tc['auxhist22_begin'] == tc['history_begin']   # the template this stream copies

    def test_extrapolation_setting_reaches_the_namelist(self, mock_params, tmp_path):
        mock_params['time_control']['p_level_file'] = {**C1_BLOCK, 'extrap_below_grnd': 2}
        set_nml_params()
        assert f90nml.read(tmp_path / 'namelist.input')['diags']['extrap_below_grnd'] == 2

    def test_history_interval_zero_is_refused(self, mock_params):
        mock_params['time_control']['history_file']['interval_hours'] = [1, 0, 1]
        mock_params['time_control']['p_level_file'] = dict(C1_BLOCK)
        with pytest.raises(ValueError, match='history interval 0'):
            set_nml_params()

    def test_top_level_output_variables_refuses_the_run(self, mock_params):
        """The migration refusal fires in set_nml_params, i.e. before any preprocessing."""
        mock_params['output_variables'] = ['T2']
        with pytest.raises(ValueError, match='moved to \\[time_control.history_file\\]'):
            set_nml_params()

    def test_bad_z_level_variable_refuses_the_run(self, mock_params):
        mock_params['time_control']['z_level_file'] = {'output': True, 'z_levels': [100],
                                                       'output_variables': ['T_PL']}
        with pytest.raises(ValueError, match='not written to this file'):
            set_nml_params()


# ------------------------------------------------------------------------------- resolver


class TestResolvePLevelFile:

    def test_ok_control_the_c1_block(self):
        got = utils.resolve_p_level_file(_cfg(dict(C1_BLOCK)))
        assert got['levels_pa'] == [90000.0, 85000.0, 60000.0, 50000.0, 30000.0]
        assert got['variables'] == sorted(C1_BLOCK['output_variables'])
        assert (got['extrap_below_grnd'], got['use_tot_or_hyd_p'], got['p_lev_missing']) == (1, 2, -999.0)

    def test_absent_and_off_are_none(self):
        assert utils.resolve_p_level_file(_cfg()) is None
        assert utils.resolve_p_level_file(_cfg({'output': False, 'p_levels_hpa': [850]})) is None

    @pytest.mark.parametrize('block, match', [
        ({'output': True, 'p_levels_hpa': [850], 'level': 1}, 'unknown key'),
        ({'output': 'yes', 'p_levels_hpa': [850]}, 'true or false'),
        ({'output': True}, 'non-empty list'),
        ({'output': True, 'p_levels_hpa': []}, 'non-empty list'),
        ({'output': True, 'p_levels_hpa': ['850']}, 'must be numbers'),
        ({'output': True, 'p_levels_hpa': [85000, 50000]}, 'is Pa'),
        ({'output': True, 'p_levels_hpa': [0]}, 'is Pa'),
        ({'output': True, 'p_levels_hpa': [500, 850]}, 'STRICTLY DESCENDING'),
        ({'output': True, 'p_levels_hpa': [850, 850, 500]}, 'STRICTLY DESCENDING'),
        ({'output': True, 'p_levels_hpa': [1000 - 9 * i for i in range(101)]}, 'at most 100'),
        ({'output': True, 'p_levels_hpa': [850, 50]}, 'model top'),
        ({'output': True, 'p_levels_hpa': [850, 30]}, 'model top'),
        ({'output': True, 'p_levels_hpa': [850], 'output_variables': ['W_PL']}, 'not written'),
        ({'output': True, 'p_levels_hpa': [850], 'output_variables': 'T_PL'}, 'list of variable names'),
        ({'output': True, 'p_levels_hpa': [850], 'extrap_below_grnd': 3}, 'integer 1 or 2'),
        ({'output': True, 'p_levels_hpa': [850], 'extrap_below_grnd': True}, 'integer 1 or 2'),
        ({'output': True, 'p_levels_hpa': [850], 'use_tot_or_hyd_p': 1.0}, 'integer 1 or 2'),
        ({'output': True, 'p_levels_hpa': [850], 'output_presets': ['wvt_2d']}, 'unknown key'),
    ])
    def test_refusals(self, block, match):
        with pytest.raises(ValueError, match=match):
            utils.resolve_p_level_file(_cfg(block))

    def test_p_level_file_unknown_variable_refused_even_when_off(self):
        """An off block is still parsed for its variable list: switching it on must not be the first
        time a typo surfaces."""
        with pytest.raises(ValueError, match='not written'):
            utils.resolve_p_level_file(_cfg({'output': False, 'output_variables': ['TT_PL']}))

    @pytest.mark.parametrize('extra', [
        {'diags': {'press_levels': [85000.0]}},
        {'diags': {'p_lev_diags': 1}},
    ])
    def test_raw_diags_passthrough_of_owned_keys_refused(self, extra):
        with pytest.raises(ValueError, match='set by \\[time_control.p_level_file\\]'):
            utils.resolve_p_level_file(_cfg(dict(C1_BLOCK), **extra))

    @pytest.mark.parametrize('key', ['auxhist23_interval', 'io_form_auxhist23', 'frames_per_auxhist23'])
    def test_raw_auxhist23_passthrough_refused_even_with_the_block_absent(self, key):
        """Every stream-23 key, not only the ones SPELLED auxhist23_* (io_form_/frames_per_ slipped a
        startswith() test once)."""
        cfg = _cfg()
        cfg['time_control'][key] = 2
        with pytest.raises(ValueError, match=key):
            utils.resolve_p_level_file(cfg)

    def test_model_top_follows_p_top_requested(self):
        cfg = _cfg({'output': True, 'p_levels_hpa': [850, 60]})
        assert utils.resolve_p_level_file(cfg)['levels_hpa'] == [850, 60]     # 60 hPa > 50 hPa default top
        cfg['domains']['p_top_requested'] = 10000
        with pytest.raises(ValueError, match='model top'):
            utils.resolve_p_level_file(cfg)


class TestResolveStreamVariables:

    @pytest.mark.parametrize('key', ['output_variables', 'output_presets'])
    def test_top_level_keys_refused_with_the_new_location(self, key):
        with pytest.raises(ValueError, match='moved to \\[time_control.history_file\\]'):
            utils.resolve_stream_variables({key: ['T2'], 'time_control': {}}, 'history')

    def test_history_unions_presets_and_variables(self):
        cfg = {'time_control': {'history_file': {'output_presets': ['wrf_to_int'], 'output_variables': ['OLR']}}}
        got = utils.resolve_stream_variables(cfg, 'history')
        assert 'OLR' in got and 'COSALPHA' in got

    def test_unknown_preset_refused(self):
        with pytest.raises(ValueError, match='Unknown output preset'):
            utils.resolve_stream_variables({'time_control': {'history_file': {'output_presets': 'nope'}}}, 'history')

    def test_presets_only_belong_to_history(self):
        cfg = {'time_control': {'z_level_file': {'output_presets': ['wvt_2d']}}}
        with pytest.raises(ValueError, match='presets are wrfout'):
            utils.resolve_stream_variables(cfg, 'zlevel')

    def test_no_list_is_none(self):
        for stream in defaults.OUTPUT_STREAMS:
            assert utils.resolve_stream_variables({'time_control': {}}, stream) is None

    def test_level_vector_may_be_named(self):
        cfg = {'time_control': {'p_level_file': {'output_variables': ['P_PL', 'T_PL']}}}
        assert utils.resolve_stream_variables(cfg, 'plevel') == ['P_PL', 'T_PL']


# ------------------------------------------------------------------------ file selection


def _touch(tmp_path, *names):
    for n in names:
        (tmp_path / n).write_text('x')


def test_query_out_files_finds_wrfplevels_in_both_spellings_and_never_wrfrst(tmp_path):
    _touch(tmp_path, 'wrfplevels_d01_2023-02-12_00:00:00.nc', 'wrfplevels_d01_2023-02-13_00_00_00.nc',
           'wrfrst_d01_2023-02-13_00:00:00')
    found = utils.query_out_files(tmp_path)
    assert set(found) == {('wrfplevels', 'd01')}
    assert len(found[('wrfplevels', 'd01')]) == 2


def test_prefixes_come_from_the_stream_table():
    assert set(utils.output_prefixes()) == {'wrfout_d', 'wrfxtrm_d', 'wrfzlevels_d', 'wrfplevels_d'}


class TestEndFrame:
    """The midnight end frame is dropped BY NAME. "Skip the newest per group" dropped a complete day
    from any stream without an end file (review plev-autoruns-plan-1, finding 8)."""

    END = datetime.datetime(2023, 2, 14)

    def _day_names(self, prefix, n=3, end_file=True):
        names = [f'{prefix}_d01_2023-02-{11 + d:02d}_00:00:00.nc' for d in range(n)]
        if end_file:
            names.append(f'{prefix}_d01_2023-02-14_00:00:00.nc')
        return names

    def test_c1_case_unchanged_every_stream_loses_only_its_end_file(self, tmp_path, monkeypatch):
        import monitor_wrf
        names = self._day_names('wrfout') + self._day_names('wrfplevels')
        _touch(tmp_path, *names)
        monkeypatch.setattr(params, 'upload_end_frame', False)
        picked = {os.path.basename(p) for p in monitor_wrf.post_run_files(tmp_path, self.END)}
        assert picked == {n for n in names if '2023-02-14' not in n}

    def test_a_stream_without_an_end_file_keeps_its_last_complete_day(self, tmp_path, monkeypatch):
        import monitor_wrf
        _touch(tmp_path, *self._day_names('wrfout'), *self._day_names('wrfplevels', end_file=False))
        monkeypatch.setattr(params, 'upload_end_frame', False)
        picked = {os.path.basename(p) for p in monitor_wrf.post_run_files(tmp_path, self.END)}
        assert 'wrfplevels_d01_2023-02-13_00:00:00.nc' in picked
        assert 'wrfout_d01_2023-02-14_00:00:00.nc' not in picked

    def test_already_renamed_end_file_is_also_matched(self, tmp_path):
        _touch(tmp_path, 'wrfplevels_d01_2023-02-13_00_00_00.nc', 'wrfplevels_d01_2023-02-14_00_00_00.nc')
        got = utils.drop_end_frame_files(utils.query_out_files(tmp_path), self.END)
        assert [os.path.basename(p) for p in got[('wrfplevels', 'd01')]] == ['wrfplevels_d01_2023-02-13_00_00_00.nc']

    def test_wrfxtrm_end_file_is_kept(self, tmp_path):
        _touch(tmp_path, 'wrfxtrm_d01_2023-02-14_00:00:00.nc')
        got = utils.drop_end_frame_files(utils.query_out_files(tmp_path, include_xtrm=True), self.END)
        assert len(got[('wrfxtrm', 'd01')]) == 1


# -------------------------------------------------------------------------------- filter

# Synthetic files built the way WRF builds these streams: every *_PL at every level, P_PL as
# (Time, level), the {22}{23} surface fields, XLAT/XLONG/Times -- and NO XTIME (Registry.EM_COMMON:
# XTIME is not in stream 23; `ncks -v XTIME` on such a file exits 1).
LEVELS_PA = [90000.0, 85000.0, 60000.0, 50000.0, 30000.0]
ALL_PL = ('P_PL',) + defaults.PLEVEL_VARIABLES


def _write_wrf_like(path, variables, n_time=2, n_lev=5, xtime=False, attrs=None):
    ny, nx = 3, 4
    rng = np.random.default_rng(0)
    with h5netcdf.File(path, 'w') as f:
        f.dimensions = {'Time': n_time, 'DateStrLen': 19, 'num_press_levels_stag': n_lev,
                        'south_north': ny, 'west_east': nx}
        times = np.array([list(f'2023-02-12_{3 * t:02d}:00:00') for t in range(n_time)], dtype='S1')
        f.create_variable('Times', ('Time', 'DateStrLen'), data=times)
        for name in ('XLAT', 'XLONG', 'Q2', 'T2', 'U10', 'V10'):
            f.create_variable(name, ('Time', 'south_north', 'west_east'), data=rng.random((n_time, ny, nx)).astype('f4'))
        if xtime:
            f.create_variable('XTIME', ('Time',), data=np.arange(n_time, dtype='f4'))
        for name in variables:
            if name == 'P_PL':
                f.create_variable('P_PL', ('Time', 'num_press_levels_stag'),
                                  data=np.tile(np.array(LEVELS_PA[:n_lev], dtype='f4'), (n_time, 1)))
            else:
                f.create_variable(name, ('Time', 'num_press_levels_stag', 'south_north', 'west_east'),
                                  data=rng.random((n_time, n_lev, ny, nx)).astype('f4'))
        f.attrs.update(attrs or {'MAP_PROJ': np.int32(1), 'TRUELAT1': np.float32(-41.236)})


def _write_wrfout_like(path, variables=('T2', 'Q2', 'SLP', 'OLR')):
    ny, nx = 3, 4
    with h5netcdf.File(path, 'w') as f:
        f.dimensions = {'Time': 1, 'DateStrLen': 19, 'south_north': ny, 'west_east': nx}
        f.create_variable('Times', ('Time', 'DateStrLen'),
                          data=np.array([list('2023-02-12_00:00:00')], dtype='S1'))
        for name in ('XLAT', 'XLONG', *variables):
            f.create_variable(name, ('Time', 'south_north', 'west_east'), data=np.ones((1, ny, nx), 'f4'))
        f.create_variable('XTIME', ('Time',), data=np.zeros(1, 'f4'))


def _vars(path):
    with h5netcdf.File(path, 'r') as f:
        return set(f.variables)


@pytest.fixture()
def recorded_prune(monkeypatch):
    """Record what would be handed to ncks, without needing ncks (the real-ncks test is below)."""
    calls = {}
    monkeypatch.setattr(utils, '_ncks_prune', lambda path, names: calls.__setitem__(os.path.basename(path), list(names)))
    return calls


def _deliver(files, tmp_path, monkeypatch):
    """Everything deliver_output_files does, with the upload faked -- returns what was handed on."""
    import monitor_wrf
    handed = []
    monkeypatch.setattr(params, 'output_hook', None)
    monkeypatch.setattr(utils, 'ul_output_files', lambda fs, *a: handed.extend(os.path.basename(f) for f in fs))
    monitor_wrf.deliver_output_files(files, str(tmp_path), {':': '_'}, 'output', '/out')
    return handed


class TestFilterThroughTheDeliverySeam:

    def _files(self, tmp_path, plevel_vars=ALL_PL, **kw):
        out = str(tmp_path / 'wrfout_d01_2023-02-12_00:00:00.nc')
        plv = str(tmp_path / 'wrfplevels_d01_2023-02-12_00:00:00.nc')
        _write_wrfout_like(out)
        _write_wrf_like(plv, plevel_vars, **kw)
        return out, plv

    def test_p_level_prune_runs_with_no_wrfout_list(self, tmp_path, monkeypatch, recorded_prune, no_sentry):
        out, plv = self._files(tmp_path)
        monkeypatch.setattr(params, 'file', _cfg(dict(C1_BLOCK)))
        handed = _deliver([out, plv], tmp_path, monkeypatch)
        assert set(recorded_prune) == {'wrfplevels_d01_2023-02-12_00:00:00.nc'}   # wrfout untouched
        assert set(recorded_prune['wrfplevels_d01_2023-02-12_00:00:00.nc']) == KEPT_C1  # no XTIME
        assert sorted(handed) == ['wrfout_d01_2023-02-12_00_00_00.nc', 'wrfplevels_d01_2023-02-12_00_00_00.nc']

    def test_wrfout_list_does_not_prune_the_p_level_file(self, tmp_path, monkeypatch, recorded_prune, no_sentry):
        out, plv = self._files(tmp_path)
        cfg = _cfg({'output': True, 'p_levels_hpa': [900, 850, 600, 500, 300]})
        cfg['time_control']['history_file']['output_variables'] = ['T2']
        monkeypatch.setattr(params, 'file', cfg)
        _deliver([out, plv], tmp_path, monkeypatch)
        assert set(recorded_prune) == {'wrfout_d01_2023-02-12_00:00:00.nc'}
        assert {'T2', 'Times', 'XLAT', 'XLONG', 'XTIME'} == set(recorded_prune['wrfout_d01_2023-02-12_00:00:00.nc'])

    def test_missing_requested_field_delivers_unpruned_and_never_blocks_wrfout(
            self, tmp_path, monkeypatch, recorded_prune, no_sentry, capsys):
        out, plv = self._files(tmp_path, plevel_vars=[v for v in ALL_PL if v != 'Q_PL'])
        cfg = _cfg(dict(C1_BLOCK))
        cfg['time_control']['history_file']['output_variables'] = ['T2']
        monkeypatch.setattr(params, 'file', cfg)
        handed = _deliver([plv, out], tmp_path, monkeypatch)     # p-level first: it must not stop wrfout
        assert 'wrfplevels_d01_2023-02-12_00:00:00.nc' not in recorded_prune
        assert 'wrfout_d01_2023-02-12_00:00:00.nc' in recorded_prune
        assert len(handed) == 2
        assert "requested ['Q_PL'] not in the file; delivering it UNPRUNED" in capsys.readouterr().out

    def test_ncks_failure_on_an_aux_file_delivers_it_unpruned(self, tmp_path, monkeypatch, no_sentry):
        out, plv = self._files(tmp_path)
        monkeypatch.setattr(params, 'file', _cfg(dict(C1_BLOCK)))

        def fail(path, names):
            raise subprocess.CalledProcessError(1, 'ncks', stderr='boom')
        monkeypatch.setattr(utils, '_ncks_prune', fail)
        handed = _deliver([out, plv], tmp_path, monkeypatch)
        assert len(handed) == 2 and ALL_PL[1] in _vars(tmp_path / 'wrfplevels_d01_2023-02-12_00_00_00.nc')

    def test_provenance_is_stamped_even_without_a_prune(self, tmp_path, monkeypatch, recorded_prune, no_sentry):
        out, plv = self._files(tmp_path)
        monkeypatch.setattr(params, 'file', _cfg({'output': True, 'p_levels_hpa': [900, 850, 600, 500, 300],
                                                  'extrap_below_grnd': 2}))   # no output_variables
        _deliver([out, plv], tmp_path, monkeypatch)
        assert recorded_prune == {}
        with h5netcdf.File(tmp_path / 'wrfplevels_d01_2023-02-12_00_00_00.nc', 'r') as f:
            assert int(f.attrs['p_lev_extrap_below_grnd']) == 2
            assert list(f.attrs['p_lev_press_levels_pa']) == LEVELS_PA

    def test_config_error_raises_before_any_file_is_touched(self, tmp_path, monkeypatch, recorded_prune):
        out, plv = self._files(tmp_path)
        cfg = _cfg(dict(C1_BLOCK))
        cfg['output_variables'] = ['T2']          # the moved key
        monkeypatch.setattr(params, 'file', cfg)
        with pytest.raises(ValueError, match='moved'):
            _deliver([out, plv], tmp_path, monkeypatch)
        assert recorded_prune == {}


@pytest.mark.skipif(shutil.which('ncks') is None and not os.environ.get('REQUIRE_NCKS'),
                    reason='needs NCO (ncks); set REQUIRE_NCKS=1 (the mutation pass does) to make its absence FAIL')
def test_real_ncks_keeps_every_level_and_exactly_the_requested_fields(tmp_path, monkeypatch, no_sentry):
    """No mock_params here: that fixture stubs subprocess.run, which would make this a no-op."""
    plv = str(tmp_path / 'wrfplevels_d01_2023-02-12_00:00:00.nc')
    _write_wrf_like(plv, ALL_PL, n_time=8)
    with h5netcdf.File(plv, 'r') as f:
        t_before = f['T_PL'][...].copy()
        attrs_before = dict(f.attrs)
    monkeypatch.setattr(params, 'file', _cfg(dict(C1_BLOCK)))
    assert utils.filter_output_files([plv]) == [plv]
    assert _vars(plv) == KEPT_C1
    with h5netcdf.File(plv, 'r') as f:
        assert f['T_PL'].shape == (8, 5, 3, 4)                      # every level of a kept variable
        assert f['T_PL'].compression == 'gzip' and f['T_PL'].compression_opts == 1   # the pipeline's -L 1
        np.testing.assert_array_equal(f['T_PL'][...], t_before)
        assert list(f['P_PL'][0]) == LEVELS_PA
        assert b''.join(f['Times'][3]).decode() == '2023-02-12_09:00:00'   # WRF's char Times survive the prune
        for key in attrs_before:                                    # the projection header survives
            assert key in f.attrs
        assert list(f.attrs['p_lev_press_levels_pa']) == LEVELS_PA
        assert int(f.attrs['p_lev_extrap_below_grnd']) == 1 and int(f.attrs['p_lev_use_tot_or_hyd_p']) == 2
        assert float(f.attrs['p_lev_missing']) == -999.0


# ------------------------------------------------ review plev-autoruns-code-2: the gaps the arms found
# Each test below exists because a mutant survived the suite: the stream it guards had no test at all.


class TestCodeReviewFollowUps:

    @pytest.mark.parametrize('stream, block', [('history', 'history_file'), ('summary', 'summary_file'),
                                               ('zlevel', 'z_level_file'), ('plevel', 'p_level_file')])
    def test_empty_output_variables_refused(self, stream, block):
        with pytest.raises(ValueError, match='omit the key'):
            utils.resolve_stream_variables({'time_control': {block: {'output_variables': []}}}, stream)

    @pytest.mark.parametrize('block, match', [
        ({'p_levels_hpa': [850]}, 'explicit `output'),
        ({'output': 1, 'p_levels_hpa': [850]}, 'true or false'),
        ({'output': True, 'p_levels_hpa': [850], 'use_tot_or_hyd_p': 3}, 'integer 1 or 2'),
        ({'output': True, 'p_levels_hpa': [850], 'extrap_below_grnd': 0}, 'integer 1 or 2'),
    ])
    def test_more_refusals(self, block, match):
        with pytest.raises(ValueError, match=match):
            utils.resolve_p_level_file(_cfg(block))

    def test_shared_diags_keys_are_the_blocks_only_when_it_exists(self):
        """extrap_below_grnd is read by zld too: a z-level-only config may set it raw."""
        assert utils.resolve_p_level_file(_cfg(None, diags={'extrap_below_grnd': 2})) is None
        with pytest.raises(ValueError, match='extrap_below_grnd'):
            utils.resolve_p_level_file(_cfg(dict(C1_BLOCK), diags={'extrap_below_grnd': 2}))
        with pytest.raises(ValueError, match='p_lev_diags'):
            utils.resolve_p_level_file(_cfg(None, diags={'p_lev_diags': 1}))

    def test_fixed_time_step_must_divide_the_output_interval(self, mock_params, tmp_path):
        """dx 27 km -> dt 162 s (nests 54, 18). 1 h is not a whole number of 162 s steps; 9 h is."""
        mock_params['domains']['use_adaptive_time_step'] = False
        mock_params['time_control']['p_level_file'] = dict(C1_BLOCK)
        with pytest.raises(ValueError, match='whole number of time steps'):
            set_nml_params()
        mock_params['time_control']['history_file']['interval_hours'] = [9, 9, 9]
        set_nml_params()
        assert f90nml.read(tmp_path / 'namelist.input')['diags']['p_lev_diags'] == 1

    def test_block_never_leaks_into_time_control(self, mock_params, tmp_path):
        mock_params['time_control']['p_level_file'] = dict(C1_BLOCK)
        set_nml_params()
        assert 'p_level_file' not in (tmp_path / 'namelist.input').read_text()

    def test_unreadable_aux_file_is_withheld_and_never_blocks_wrfout(self, tmp_path, monkeypatch, recorded_prune,
                                                                      no_sentry, capsys):
        out = str(tmp_path / 'wrfout_d01_2023-02-12_00:00:00.nc')
        plv = str(tmp_path / 'wrfplevels_d01_2023-02-12_00:00:00.nc')
        _write_wrfout_like(out)
        open(plv, 'wb').write(b'\x89HDF\r\n truncated')
        cfg = _cfg(dict(C1_BLOCK))
        cfg['time_control']['history_file']['output_variables'] = ['T2']
        monkeypatch.setattr(params, 'file', cfg)
        monkeypatch.setattr(utils, '_unreadable_alerted', set())
        handed = _deliver([plv, out], tmp_path, monkeypatch)
        assert handed == ['wrfout_d01_2023-02-12_00_00_00.nc']
        assert os.path.exists(plv)                                   # kept for a retry, not deleted
        assert 'cannot be opened' in capsys.readouterr().out

    def test_wrfout_d02_lacking_a_field_warns_and_ncks_gets_only_what_it_has(self, tmp_path, monkeypatch,
                                                                            recorded_prune, no_sentry):
        d1 = str(tmp_path / 'wrfout_d01_2023-02-12_00:00:00.nc')
        d2 = str(tmp_path / 'wrfout_d02_2023-02-12_00:00:00.nc')
        _write_wrfout_like(d1, ('T2', 'OLR'))
        _write_wrfout_like(d2, ('T2',))
        cfg = _cfg()
        cfg['time_control']['history_file']['output_variables'] = ['T2', 'OLR']
        monkeypatch.setattr(params, 'file', cfg)
        _deliver([d1, d2], tmp_path, monkeypatch)                    # no raise
        assert 'OLR' in recorded_prune['wrfout_d01_2023-02-12_00:00:00.nc']
        assert 'OLR' not in recorded_prune['wrfout_d02_2023-02-12_00:00:00.nc']

    def test_wrfout_list_gets_3d_auxiliaries_and_wvt_expansion(self, tmp_path, monkeypatch, recorded_prune, no_sentry):
        out = str(tmp_path / 'wrfout_d01_2023-02-12_00:00:00.nc')
        _write_wrfout_like(out, ('QVAPOR', 'P', 'PB', 'PH', 'PHB', 'HGT', 'qv_tr', 'qv_tr_02'))
        cfg = _cfg(dynamics={'tracer_opt': 4},
                   wvt={'regions': [{'name': 'a', 'bbox_deg': [-50, -30, 160, 180]},
                                    {'name': 'b', 'bbox_deg': [-30, -10, 160, 180]}]})
        cfg['time_control']['history_file']['output_variables'] = ['QVAPOR', 'qv_tr']
        monkeypatch.setattr(params, 'file', cfg)
        _deliver([out], tmp_path, monkeypatch)
        assert {'P', 'PB', 'PH', 'PHB', 'HGT', 'qv_tr', 'qv_tr_02'} <= set(recorded_prune['wrfout_d01_2023-02-12_00:00:00.nc'])

    def test_wrfxtrm_and_wrfzlevels_follow_their_own_lists(self, tmp_path, monkeypatch, recorded_prune, no_sentry):
        xt = str(tmp_path / 'wrfxtrm_d01_2023-02-13_00:00:00.nc')
        zl = str(tmp_path / 'wrfzlevels_d01_2023-02-12_00:00:00.nc')
        _write_wrfout_like(xt, ('T2MAX', 'T2MIN', 'T2'))
        _write_wrfout_like(zl, ('Z_ZL', 'U_ZL', 'V_ZL', 'T_ZL'))
        cfg = _cfg()
        cfg['time_control']['history_file']['output_variables'] = ['T2']
        cfg['time_control']['summary_file'] = {'output': True, 'output_variables': ['T2MAX']}
        cfg['time_control']['z_level_file'] = {'output': True, 'z_levels': [100], 'output_variables': ['U_ZL']}
        monkeypatch.setattr(params, 'file', cfg)
        _deliver([xt, zl], tmp_path, monkeypatch)
        assert set(recorded_prune['wrfxtrm_d01_2023-02-13_00:00:00.nc']) == {'T2MAX', 'Times', 'XLAT', 'XLONG', 'XTIME'}
        assert set(recorded_prune['wrfzlevels_d01_2023-02-12_00:00:00.nc']) == {'U_ZL', 'Z_ZL', 'Times', 'XLAT', 'XLONG', 'XTIME'}  # XTIME: the helper writes it

    def test_every_p_level_file_is_stamped_and_no_z_level_file_is(self, tmp_path, monkeypatch, recorded_prune, no_sentry):
        files = []
        for d in (11, 12):
            f = str(tmp_path / f'wrfplevels_d01_2023-02-{d}_00:00:00.nc')
            _write_wrf_like(f, ALL_PL)
            files.append(f)
        zl = str(tmp_path / 'wrfzlevels_d01_2023-02-12_00:00:00.nc')
        _write_wrfout_like(zl, ('Z_ZL', 'U_ZL'))
        cfg = _cfg(dict(C1_BLOCK))
        cfg['time_control']['z_level_file'] = {'output': True, 'z_levels': [100]}
        monkeypatch.setattr(params, 'file', cfg)
        _deliver(files + [zl], tmp_path, monkeypatch)
        for d in (11, 12):
            with h5netcdf.File(tmp_path / f'wrfplevels_d01_2023-02-{d}_00_00_00.nc', 'r') as f:
                assert 'p_lev_press_levels_pa' in f.attrs
        with h5netcdf.File(tmp_path / 'wrfzlevels_d01_2023-02-12_00_00_00.nc', 'r') as f:
            assert not any(k.startswith('p_lev_') for k in f.attrs)

    @pytest.mark.parametrize('final, remote', [(True, True), (False, False)])
    def test_post_run_and_hook_only_delivery_prune_too(self, tmp_path, monkeypatch, recorded_prune, no_sentry,
                                                        final, remote):
        import monitor_wrf
        plv = str(tmp_path / 'wrfplevels_d01_2023-02-12_00:00:00.nc')
        _write_wrf_like(plv, ALL_PL)
        monkeypatch.setattr(params, 'file', _cfg(dict(C1_BLOCK)))
        monkeypatch.setattr(utils, 'ul_output_files', lambda fs, *a: None)
        monkeypatch.setattr(utils, 'hook_output_files', lambda fs, retry_failed=False: fs)
        monkeypatch.setattr(params, 'output_hook', None if remote else {'match': '*'})
        monitor_wrf.deliver_output_files([plv], str(tmp_path), {':': '_'}, 'output' if remote else None,
                                         '/out' if remote else None, final=final)
        assert 'wrfplevels_d01_2023-02-12_00:00:00.nc' in recorded_prune

    def test_nested_domain_end_frame_is_dropped_by_name(self, tmp_path):
        _touch(tmp_path, 'wrfout_d02_2023-02-13_00:00:00.nc', 'wrfout_d02_2023-02-14_00:00:00.nc',
               'wrfplevels_d02_2023-02-13_00:00:00.nc')
        got = utils.drop_end_frame_files(utils.query_out_files(tmp_path), datetime.datetime(2023, 2, 14))
        assert [os.path.basename(p) for p in got[('wrfout', 'd02')]] == ['wrfout_d02_2023-02-13_00:00:00.nc']
        assert len(got[('wrfplevels', 'd02')]) == 1

    def test_alerts_reach_sentry_when_configured(self, tmp_path, monkeypatch, recorded_prune):
        sent = []
        monkeypatch.setattr(params, 'is_sentry', True)
        monkeypatch.setattr(utils.sentry_sdk, 'capture_message', lambda msg, level=None: sent.append((msg, level)))
        plv = str(tmp_path / 'wrfplevels_d01_2023-02-12_00:00:00.nc')
        _write_wrf_like(plv, [v for v in ALL_PL if v != 'Q_PL'])
        monkeypatch.setattr(params, 'file', _cfg(dict(C1_BLOCK)))
        utils.filter_output_files([plv])
        assert sent and sent[0][1] == 'warning' and 'Q_PL' in sent[0][0]
