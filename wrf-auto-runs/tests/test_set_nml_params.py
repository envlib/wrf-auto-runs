import datetime

import f90nml

import defaults
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
