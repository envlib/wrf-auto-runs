import datetime

import f90nml
import pendulum
import pytest

import defaults
import params
from set_params import set_nml_params


class TestSetNmlParams:
    def test_all_domains_basic(self, mock_params, tmp_path):
        """All 3 domains, no physics/dynamics overrides."""
        start, end, interval_hours, output_files = set_nml_params()

        # ── Return values ──
        assert start == datetime.datetime(2020, 1, 1, 0, 0, 0)
        assert end == datetime.datetime(2020, 1, 3, 0, 0, 0)
        assert interval_hours == 3
        assert len(output_files) == 6  # 2 days x 3 domains

        # ── Read back namelists ──
        wps = f90nml.read(tmp_path / 'namelist.wps')
        wrf = f90nml.read(tmp_path / 'namelist.input')

        # WPS &share
        assert wps['share']['max_dom'] == 3
        assert wps['share']['interval_seconds'] == 10800
        assert len(wps['share']['start_date']) == 3
        assert len(wps['share']['end_date']) == 3

        # WPS &geogrid
        assert wps['geogrid']['dx'] == 27000
        assert wps['geogrid']['parent_id'] == [1, 1, 2]
        assert len(wps['geogrid']['e_we']) == 3

        # WRF &domains
        assert wrf['domains']['time_step'] == 162  # 27000 * 0.001 * 6
        assert wrf['domains']['max_dom'] == 3
        assert wrf['domains']['grid_id'] == [1, 2, 3]
        assert wrf['domains']['parent_time_step_ratio'][0] == 1
        assert wrf['domains']['max_step_increase_pct'] == [5, 51, 51]

        # WRF &physics — all defaults, per-domain fields are length 3
        for field in defaults.PHYSICS_PER_DOMAIN_FIELDS:
            nml_val = wrf['physics'][field]
            assert isinstance(nml_val, list), f'{field} should be a list'
            assert len(nml_val) == 3, f'{field} should have length 3'

        # WRF &dynamics — all defaults, per-domain fields are length 3
        for field in defaults.DYNAMICS_PER_DOMAIN_FIELDS:
            nml_val = wrf['dynamics'][field]
            assert isinstance(nml_val, list), f'{field} should be a list'
            assert len(nml_val) == 3, f'{field} should have length 3'

        # WRF &time_control arrays
        assert len(wrf['time_control']['history_interval']) == 3
        assert len(wrf['time_control']['frames_per_outfile']) == 3

    def test_domain_subset(self, mock_params, tmp_path):
        """Run domains=[1,2] from 3 defined."""
        start, end, interval_hours, output_files = set_nml_params(domains=[1, 2])

        wps = f90nml.read(tmp_path / 'namelist.wps')
        wrf = f90nml.read(tmp_path / 'namelist.input')

        assert wps['share']['max_dom'] == 2
        assert wrf['domains']['max_dom'] == 2
        assert wrf['domains']['grid_id'] == [1, 2]

        # Per-domain arrays all have length 2
        assert len(wrf['time_control']['history_interval']) == 2
        assert len(wrf['domains']['e_vert']) == 2
        assert len(wrf['physics']['mp_physics']) == 2
        assert len(wrf['dynamics']['non_hydrostatic']) == 2

        # 2 days x 2 domains = 4 output files
        assert len(output_files) == 4

    def test_physics_override(self, mock_params, tmp_path):
        """[physics] with cu_physics array override."""
        mock_params['physics'] = {'cu_physics': [16, 16, 0]}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['cu_physics'] == [16, 16, 0]
        # Other defaults preserved
        assert wrf['physics']['mp_physics'] == [6, 6, 6]

    def test_physics_scalar_broadcast(self, mock_params, tmp_path):
        """[physics] with scalar override broadcasts to all domains."""
        mock_params['physics'] = {'mp_physics': 8}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['mp_physics'] == [8, 8, 8]

    def test_dynamics_override(self, mock_params, tmp_path):
        """[dynamics] overrides applied, defaults preserved."""
        mock_params['dynamics'] = {'diff_opt': [1, 1, 2]}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['dynamics']['diff_opt'] == [1, 1, 2]
        # Default preserved
        assert wrf['dynamics']['hybrid_opt'] == 2

    def test_physics_extra_keys(self, mock_params, tmp_path):
        """[physics] accepts arbitrary WRF &physics keys like topo_wind."""
        mock_params['physics'] = {'topo_wind': [0, 1, 2]}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['topo_wind'] == [0, 1, 2]

    def test_domains_passthrough(self, mock_params, tmp_path):
        """Unknown keys in [domains] pass through to WRF &domains."""
        mock_params['domains']['use_adaptive_time_step'] = False

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['domains']['use_adaptive_time_step'] is False

    def test_fdda_section(self, mock_params, tmp_path):
        """[fdda] keys pass directly to WRF &fdda."""
        mock_params['fdda'] = {'grid_fdda': 1}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['fdda']['grid_fdda'] == 1

    def test_summary_and_zlevel_output(self, mock_params, tmp_path):
        """Enable both summary_file and z_level_file."""
        mock_params['time_control']['summary_file'] = {
            'output': True,
            'interval_days': 1,
            'n_days_per_file': 1,
        }
        mock_params['time_control']['z_level_file'] = {
            'output': True,
            'z_levels': [100, 200, 500],
        }

        start, end, interval_hours, output_files = set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')

        # Summary file
        assert wrf['time_control']['output_diagnostics'] == 1
        assert wrf['time_control']['auxhist3_interval'] == [1440, 1440, 1440]
        assert wrf['time_control']['frames_per_auxhist3'] == [1, 1, 1]

        # Z-level file
        assert wrf['diags']['z_lev_diags'] == 1
        assert wrf['diags']['z_levels'] == [-100, -200, -500]
        assert wrf['diags']['num_z_levels'] == 3
        assert 'auxhist22_interval' in wrf['time_control']

        # Output files include wrfxtrm and wrfzlevels entries
        prefixes = {f.split('_d')[0] for f in output_files}
        assert 'wrfout' in prefixes
        assert 'wrfxtrm' in prefixes
        assert 'wrfzlevels' in prefixes

    def test_domain_subset_slices_overrides(self, mock_params, tmp_path):
        """domains=[1,3] with cu_physics=[16,16,0] slices to [16,0]."""
        mock_params['physics'] = {'cu_physics': [16, 16, 0]}

        set_nml_params(domains=[1, 3])

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['cu_physics'] == [16, 0]
        assert wrf['domains']['max_dom'] == 2


@pytest.fixture()
def reset_chunked_flag():
    """Ensure params._chunked_mode_active doesn't bleed across tests."""
    params._chunked_mode_active = False
    yield
    params._chunked_mode_active = False


class TestChunkedBeginHours:
    """Verify history_begin and namelist start_* are chunk-aware when begin_hours > 0.

    Setup mirrors the user's 1-year run: user_start = 2022-07-01, end = 2022-07-22 (short
    so tests stay fast), interval_days = 7, begin_hours = 672 (4-week spin-up). Real WRF
    start = user_start - 672 h = 2022-06-03. Spin-up spans chunks 1-4; output starts in
    chunk 5.
    """

    USER_START = pendulum.datetime(2022, 7, 1, 0, 0, 0)
    REAL_START = USER_START.subtract(hours=672)  # 2022-06-03 00:00:00
    INTERVAL_HOURS = 7 * 24

    def _set_chunk_user_config(self, mock_params, chunk_start, chunk_end, remaining_begin_h):
        """Apply the user-style config + simulate main.py's per-chunk mutation."""
        mock_params['time_control']['start_date'] = self.USER_START.strftime('%Y-%m-%d %H:%M:%S')
        mock_params['time_control']['end_date'] = '2022-07-22 00:00:00'
        mock_params['time_control'].pop('duration_hours', None)
        mock_params['time_control']['history_file']['begin_hours'] = 672
        params._original_begin_hours = 672
        # main.py would call params.set_chunk_dates here; replicate its mutations directly
        # (so the test exercises set_nml_params in isolation, not the full helper).
        mock_params['time_control']['start_date'] = chunk_start.strftime('%Y-%m-%d %H:%M:%S')
        mock_params['time_control']['end_date'] = chunk_end.strftime('%Y-%m-%d %H:%M:%S')
        mock_params['time_control']['history_file']['begin_hours'] = remaining_begin_h
        params._chunked_mode_active = True

    def test_cold_chunk_1(self, mock_params, tmp_path, reset_chunked_flag):
        """Chunk 1 cold start: namelist start = real_start, history_begin = 40320 min."""
        chunk_start = self.REAL_START
        chunk_end = chunk_start.add(days=7)
        self._set_chunk_user_config(mock_params, chunk_start, chunk_end, 672)

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['time_control']['history_begin'] == [40320, 40320, 40320]
        assert wrf['time_control']['start_year'][0]  == chunk_start.year
        assert wrf['time_control']['start_month'][0] == chunk_start.month
        assert wrf['time_control']['start_day'][0]   == chunk_start.day
        assert wrf['time_control']['start_hour'][0]  == chunk_start.hour

    def test_mid_spinup_chunk_2(self, mock_params, tmp_path, reset_chunked_flag):
        """Chunk 2 (real_start + 168h): remaining = 504, history_begin = 30240 min."""
        chunk_start = self.REAL_START.add(hours=168)
        chunk_end = chunk_start.add(days=7)
        self._set_chunk_user_config(mock_params, chunk_start, chunk_end, 504)

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['time_control']['history_begin'] == [30240, 30240, 30240]
        assert wrf['time_control']['start_year'][0]  == chunk_start.year
        assert wrf['time_control']['start_month'][0] == chunk_start.month
        assert wrf['time_control']['start_day'][0]   == chunk_start.day

    def test_last_spinup_chunk_4(self, mock_params, tmp_path, reset_chunked_flag):
        """Chunk 4 (real_start + 504h): remaining = 168, history_begin = 10080 min."""
        chunk_start = self.REAL_START.add(hours=504)
        chunk_end = chunk_start.add(days=7)
        self._set_chunk_user_config(mock_params, chunk_start, chunk_end, 168)

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['time_control']['history_begin'] == [10080, 10080, 10080]

    def test_first_output_chunk_5(self, mock_params, tmp_path, reset_chunked_flag):
        """Chunk 5 (= user_start, spin-up complete): history_begin = 0, start = user_start."""
        chunk_start = self.USER_START  # = REAL_START + 672h
        chunk_end = chunk_start.add(days=7)
        self._set_chunk_user_config(mock_params, chunk_start, chunk_end, 0)

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['time_control']['history_begin'] == [0, 0, 0]
        assert wrf['time_control']['start_day'][0]   == self.USER_START.day
        assert wrf['time_control']['start_month'][0] == self.USER_START.month

    def test_past_spinup_chunk_clamped(self, mock_params, tmp_path, reset_chunked_flag):
        """Chunk past spin-up boundary: remaining clamped to 0; history_begin = 0."""
        chunk_start = self.REAL_START.add(hours=840)  # past the 672h boundary
        chunk_end = chunk_start.add(days=7)
        self._set_chunk_user_config(mock_params, chunk_start, chunk_end, 0)

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['time_control']['history_begin'] == [0, 0, 0]
        assert wrf['time_control']['start_day'][0] == chunk_start.day

    def test_single_stage_regression(self, mock_params, tmp_path, reset_chunked_flag):
        """Regression: when set_chunk_dates was NOT called, the existing single-stage
        subtraction still applies — start_* gets pulled back by begin_hours."""
        # Apply user-style config with begin_hours=672 but DO NOT touch _chunked_mode_active
        # (the fixture left it False).
        mock_params['time_control']['start_date'] = self.USER_START.strftime('%Y-%m-%d %H:%M:%S')
        mock_params['time_control']['end_date'] = '2022-07-22 00:00:00'
        mock_params['time_control'].pop('duration_hours', None)
        mock_params['time_control']['history_file']['begin_hours'] = 672

        assert params._chunked_mode_active is False  # sanity

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['time_control']['history_begin'] == [40320, 40320, 40320]
        # start_* should be pulled back to REAL_START (user_start - 672h = 2022-06-03)
        assert wrf['time_control']['start_year'][0]  == self.REAL_START.year
        assert wrf['time_control']['start_month'][0] == self.REAL_START.month
        assert wrf['time_control']['start_day'][0]   == self.REAL_START.day
