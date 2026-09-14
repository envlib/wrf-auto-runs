import datetime

import f90nml
import pendulum
import pytest

import defaults
import params
import set_params
from set_params import set_nml_params, validate_wvt_regions


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

        # WRF &dynamics — always-on per-domain fields are length 3. The optional WVT tracer
        # switches are only emitted when tracer_opt=4, so they are absent in this non-WVT config.
        always_on = (defaults.DYNAMICS_PER_DOMAIN_FIELDS - defaults.WVT_DYNAMICS_PER_DOMAIN_FIELDS
                     - defaults.OPTIONAL_DYNAMICS_PER_DOMAIN_FIELDS)
        for field in always_on:
            nml_val = wrf['dynamics'][field]
            assert isinstance(nml_val, list), f'{field} should be a list'
            assert len(nml_val) == 3, f'{field} should have length 3'
        for field in defaults.WVT_DYNAMICS_PER_DOMAIN_FIELDS:
            assert field not in wrf['dynamics'], f'{field} should be absent without WVT (tracer_opt!=4)'

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
        """[fdda] grid_fdda is broadcast per-domain (masked by grid_fdda>0)."""
        mock_params['fdda'] = {'grid_fdda': 1}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['fdda']['grid_fdda'] == [1, 1, 1]   # broadcast to all 3 domains

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


class TestPrecAccDt:
    """prec_acc_dt drives WRF's PREC_ACC_C/NC + SNOW_ACC_NC windowed accumulators.

    It defaults to each domain's own history_interval so every wrfout frame carries the
    precip for (frame_time - history_interval, frame_time]. That is what makes a frame
    self-contained: RAINC/RAINNC are running totals that restart at zero on every cold
    start, so an archive stitched from independent runs cannot difference across a seam.
    """

    def test_defaults_to_history_interval(self, mock_params, tmp_path):
        """No [physics] override -> prec_acc_dt mirrors history_interval, per domain."""
        mock_params['time_control']['history_file']['interval_hours'] = [24, 24, 1]

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['prec_acc_dt'] == [1440, 1440, 60]
        assert wrf['physics']['prec_acc_dt'] == wrf['time_control']['history_interval']

    def test_user_override_wins(self, mock_params, tmp_path):
        """A [physics] value must survive: `physics` is built before the setdefault."""
        mock_params['physics'] = {'prec_acc_dt': [30, 30, 30]}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['prec_acc_dt'] == [30, 30, 30]

    def test_user_scalar_is_broadcast(self, mock_params, tmp_path):
        """Scalar override broadcasts per domain (prec_acc_dt is in PHYSICS_PER_DOMAIN_FIELDS)."""
        mock_params['physics'] = {'prec_acc_dt': 30}

        set_nml_params()

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['prec_acc_dt'] == [30, 30, 30]

    def test_domain_subset_tracks_history_interval(self, mock_params, tmp_path):
        """Default follows the sliced history_interval, not the full-domain list."""
        mock_params['time_control']['history_file']['interval_hours'] = [24, 24, 1]

        set_nml_params(domains=[1, 3])

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['prec_acc_dt'] == [1440, 60]

    def test_domain_subset_slices_user_override(self, mock_params, tmp_path):
        """A full-length [physics] override is sliced to the run domains."""
        mock_params['physics'] = {'prec_acc_dt': [30, 45, 60]}

        set_nml_params(domains=[1, 3])

        wrf = f90nml.read(tmp_path / 'namelist.input')
        assert wrf['physics']['prec_acc_dt'] == [30, 60]

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


class TestValidateWvtRegions:
    """Pre-run multi-region WVT validation (mirrors WRF check_a_mundo)."""

    MULTI = {'regions': [{'name': 'west'}, {'name': 'north'}]}

    def test_no_wvt_section_ok(self):
        validate_wvt_regions({}, {}, bl_pbl=1)  # no regions -> nothing to enforce

    def test_flat_single_region_ok_with_ysu(self):
        # a single region imposes no multi-region constraints (YSU/3D allowed)
        validate_wvt_regions({'mask_type': 'ocean'}, {'tracer_opt': 4, 'tracer3dsource': 1}, bl_pbl=1)

    def test_multi_region_valid_config(self):
        dyn = {'tracer_opt': 4, 'tracer2dsource': 1, 'tracer3dsource': 0, 'tracer3dsink': 0}
        validate_wvt_regions(self.MULTI, dyn, bl_pbl=0)  # should not raise

    def test_too_many_regions(self):
        # Keyed off the constant, not a literal: the cap moves with the compiled member
        # count (8 -> 12 for the boundary-face regions), and a literal here just means
        # editing the test every time rather than testing anything.
        n = set_params.MAX_WVT_REGIONS
        wvt = {'regions': [{'name': f'r{i}'} for i in range(n + 1)]}
        with pytest.raises(ValueError, match=f'at most {n}'):
            validate_wvt_regions(wvt, {'tracer_opt': 4}, bl_pbl=0)

    def test_region_count_at_the_cap_is_accepted(self):
        # Exactly MAX regions must PASS -- the boundary of the guard itself.
        # NOT a check that MAX_WVT_REGIONS agrees with the Fortran: this test reads the same
        # constant it is testing, so reverting it to 8 leaves the whole suite green (verified
        # by mutation). That cross-repo coupling is checked by check_generators.sh in
        # wrf-docker-builds, which is the only place both trees are present.
        n = set_params.MAX_WVT_REGIONS
        wvt = {'regions': [{'name': f'r{i}'} for i in range(n)]}
        validate_wvt_regions(wvt, {'tracer_opt': 4, 'tracer2dsource': 1}, bl_pbl=0)

    def test_multi_region_requires_tracer_opt_4(self):
        with pytest.raises(ValueError, match='tracer_opt'):
            validate_wvt_regions(self.MULTI, {'tracer_opt': 0}, bl_pbl=0)

    def test_multi_region_requires_bl_pbl_0(self):
        with pytest.raises(ValueError, match='bl_pbl_physics=0'):
            validate_wvt_regions(self.MULTI, {'tracer_opt': 4}, bl_pbl=1)

    def test_multi_region_forbids_3d_source(self):
        dyn = {'tracer_opt': 4, 'tracer3dsource': 1}
        with pytest.raises(ValueError, match='tracer3dsource=0'):
            validate_wvt_regions(self.MULTI, dyn, bl_pbl=0)

    def test_multi_region_forbids_3d_sink(self):
        dyn = {'tracer_opt': 4, 'tracer3dsink': 1}
        with pytest.raises(ValueError, match='tracer3dsink'):
            validate_wvt_regions(self.MULTI, dyn, bl_pbl=0)

    def test_per_domain_list_values_handled(self):
        dyn = {'tracer_opt': [4, 4], 'tracer2dsource': [1, 1], 'tracer3dsource': [0, 0], 'tracer3dsink': [0, 0]}
        validate_wvt_regions(self.MULTI, dyn, bl_pbl=0)  # de-lists to first element; should not raise


class TestBoundaryFacesNamelist:
    """The boundary-face path through set_nml_params.

    This class exists because a dual-blind review found that path crashed with NameError on
    every config that used it, while 122 tests passed: nothing drove set_nml_params with
    boundary_faces set. Unit-testing the helpers is not the same as running the path.
    """

    @staticmethod
    def _wvt(mock_params, faces, **wvt_extra):
        mock_params['domains']['max_dom'] = 1
        mock_params['dynamics'] = {'tracer_opt': 4, 'tracer2dsource': 1,
                                   'tracer3dsource': 0, 'tracer3dsink': 0}
        mock_params['physics'] = {'bl_pbl_physics': 0}
        mock_params['wvt'] = {'mask_type': 'ocean',
                              'regions': [{'name': 'a'}, {'name': 'b'}],
                              'boundary_faces': faces, **wvt_extra}
        return mock_params

    def test_faces_reach_the_namelist(self, mock_params, tmp_path):
        # Regression: this raised NameError before _first was hoisted to module level.
        self._wvt(mock_params, ['west', 'east', 'south', 'north'])
        set_nml_params(domains=[1])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['dynamics']['num_wvt_regions'] == 6
        assert nml['dynamics']['num_wvt_bdy_regions'] == 4

    def test_no_faces_writes_zero(self, mock_params, tmp_path):
        self._wvt(mock_params, [])
        set_nml_params(domains=[1])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['dynamics']['num_wvt_bdy_regions'] == 0

    def test_widened_boundary_zone_is_accepted(self, mock_params, tmp_path):
        # spec_bdy_width=10 with relax_width unset: masks and namelist BOTH use 10, so this
        # must pass. It was refused while the check compared against the un-merged default.
        self._wvt(mock_params, ['west', 'east', 'south', 'north'])
        mock_params['bdy_control'] = {'spec_bdy_width': 10}
        set_nml_params(domains=[1])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['bdy_control']['spec_bdy_width'] == 10

    def test_mask_and_namelist_width_mismatch_is_refused(self, mock_params, tmp_path):
        # The case that actually matters: shells would cover 5 rows while WRF relaxes 10,
        # leaving rows 6-10 relaxed toward untagged LBC moisture inside the source masks.
        # This PASSED before the fix.
        self._wvt(mock_params, ['west', 'east', 'south', 'north'], relax_width=5)
        mock_params['bdy_control'] = {'spec_bdy_width': 10}
        with pytest.raises(ValueError, match='does not match the spec_bdy_width'):
            set_nml_params(domains=[1])

    def test_faces_refused_on_a_nested_run(self, mock_params, tmp_path):
        # d02 shells would be silently wrong: spec_bdy_final runs after the relabel on nests
        # and overwrites the boundary cells. create_trmask writes a mask for every domain, so
        # nothing downstream would flag it.
        #
        # ⚠ This deliberately does NOT set a max_dom key. The original test did, and that is
        # the only reason it passed: real configs define nests with parent_id arrays, so the
        # guard was reading a key that never exists and never fired. A test that injects the
        # value the guard reads proves only that the guard can read.
        self._wvt(mock_params, ['west', 'east', 'south', 'north'])
        with pytest.raises(ValueError, match='domains'):
            set_nml_params(domains=[1, 2])

    def test_faces_allowed_on_a_single_domain_run(self, mock_params, tmp_path):
        self._wvt(mock_params, ['west', 'east', 'south', 'north'])
        set_nml_params(domains=[1])
        assert f90nml.read(params.wrf_nml_path)['dynamics']['num_wvt_bdy_regions'] == 4


class TestWvtAdvectionIsWvtOnly:
    """`moist_adv_opt = 4` is a WVT requirement, not a house preference.

    It was a global default from 2026-09-13 to 2026-09-14, which silently imposed 5th-order WENO
    moisture advection on every NON-WVT run this package generates (otago, pmp, wrf_3k, forecasts)
    -- a real change to their physics, away from WRF's own default of 1, that nobody asked for.
    The tags are what require the scheme: the method's rule is that moisture and tags must use the
    SAME one (Insua-Costa & Miguez-Macho 2018), and a run with no tags has nothing to match.
    """

    @staticmethod
    def _wvt(mock_params, max_dom=1, **dyn_extra):
        mock_params['domains']['max_dom'] = max_dom
        mock_params['dynamics'] = {'tracer_opt': 4, 'tracer2dsource': 1,
                                   'tracer3dsource': 0, 'tracer3dsink': 0, **dyn_extra}
        mock_params['physics'] = {'bl_pbl_physics': 0}
        mock_params['wvt'] = {'mask_type': 'ocean', 'regions': [{'name': 'a'}, {'name': 'b'}]}
        return mock_params

    def test_non_wvt_run_keeps_wrf_default(self, mock_params, tmp_path):
        set_nml_params(domains=[1, 2, 3])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['dynamics']['moist_adv_opt'] == [1, 1, 1]
        assert 'tracer_adv_opt' not in nml['dynamics']

    def test_wvt_run_gets_weno_pd_on_both(self, mock_params, tmp_path):
        self._wvt(mock_params)
        set_nml_params(domains=[1])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['dynamics']['moist_adv_opt'] == defaults.WVT_ADV_OPT
        assert nml['dynamics']['tracer_adv_opt'] == defaults.WVT_ADV_OPT

    def test_wvt_injection_is_broadcast_to_every_domain(self, mock_params, tmp_path):
        """Regression on ORDERING: the injection must run before the per-domain broadcast.

        Set after it, both switches would reach the namelist as bare scalars while every other
        dynamics field is a max_dom-length list -- and WRF would apply them to d01 only, silently
        leaving the nests on a different advection scheme from their parent. That is the very
        mismatch the guard exists to prevent, reintroduced one domain down.
        """
        self._wvt(mock_params, max_dom=3)
        set_nml_params(domains=[1, 2, 3])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['dynamics']['moist_adv_opt'] == [defaults.WVT_ADV_OPT] * 3
        assert nml['dynamics']['tracer_adv_opt'] == [defaults.WVT_ADV_OPT] * 3

    def test_wvt_run_refuses_a_mismatched_request(self, mock_params, tmp_path):
        """Fail in Python with the reason, rather than at real.exe 20 minutes later."""
        self._wvt(mock_params, moist_adv_opt=1)
        with pytest.raises(ValueError, match='same advection scheme'):
            set_nml_params(domains=[1])

    def test_wvt_run_accepts_an_explicit_agreeing_request(self, mock_params, tmp_path):
        # Every existing WVT parameters.toml states tracer_adv_opt = 4 outright; that must keep working.
        self._wvt(mock_params, moist_adv_opt=4, tracer_adv_opt=4)
        set_nml_params(domains=[1])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['dynamics']['moist_adv_opt'] == defaults.WVT_ADV_OPT


class TestAdvectionOrderBroadcast:
    """`h_sca_adv_order`/`v_sca_adv_order` have no pipeline default, so they only appear when a
    config sets them -- as `projects/tests/wsm6_caps_vsca5` does. When set they must reach every
    domain: applied to d01 alone, a nested run would advect the parent and its nests at different
    orders, which is the parent/nest form of the mismatch the WVT guard exists to prevent.
    """

    def test_set_orders_are_broadcast(self, mock_params, tmp_path):
        mock_params['dynamics'] = {'v_sca_adv_order': 5, 'h_sca_adv_order': 5}
        set_nml_params(domains=[1, 2, 3])
        nml = f90nml.read(params.wrf_nml_path)
        assert nml['dynamics']['v_sca_adv_order'] == [5, 5, 5]
        assert nml['dynamics']['h_sca_adv_order'] == [5, 5, 5]

    def test_unset_orders_stay_absent(self, mock_params, tmp_path):
        """WRF applies its own defaults; the pipeline must not invent values it was not given."""
        set_nml_params(domains=[1, 2, 3])
        nml = f90nml.read(params.wrf_nml_path)
        assert 'v_sca_adv_order' not in nml['dynamics']
        assert 'h_sca_adv_order' not in nml['dynamics']
