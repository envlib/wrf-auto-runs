#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep 23 15:03:38 2025

@author: mike
"""
import os
import shlex
import subprocess
import pendulum
import pyproj

import params
import defaults

############################################
### Parameters


#######################################################
### Functions

def to_list(val):
    """

    """
    if not isinstance(val, list):
        val = [val]

    return val


def create_rclone_config(name, config_path, config_dict):
    """

    """
    type_ = config_dict['type']
    config_list = [f'{k}={v}' for k, v in config_dict.items() if k != 'type']
    config_str = ' '.join(config_list)
    config_path = config_path.joinpath('rclone.config')
    cmd_str = f'rclone config create {name} {type_} {config_str} --config={config_path} --non-interactive'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=True)

    return config_path


def dt_to_file_names(prefix, dts, domains):
    """
    pendulum datetimes to wrfout file names.
    """
    out_list = []
    for dt in dts:
        date_str = dt.strftime(params.wps_date_format)
        for domain in domains:
            file_name = params.outfile_format.format(prefix=prefix, domain=domain, date=date_str)
            out_list.append(file_name)

    return out_list


def read_last_line(file_path):
    """

    """
    cmd_str = f'tail -1 {file_path}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False)

    return p.stdout.strip('\n')


def query_out_files(run_path, out_files, include_xtrm=False):
    """

    """
    files = {}
    for file_path in run_path.iterdir():
        if file_path.is_file():
            file_name = file_path.name
            if file_name in out_files:
                out_name, domain, datetime = file_name.split('_', 2)
                if (out_name == 'wrfxtrm' and include_xtrm) or out_name != 'wrfxtrm':
                    if (out_name, domain) in files:
                        files[(out_name, domain)].append(str(file_path))
                        files[(out_name, domain)].sort()
                    else:
                        files[(out_name, domain)] = [str(file_path)]

    return files


# def query_out_files(run_path, output_globs):
#     """

#     """
#     out_files = {}
#     for glob in output_globs:
#         for file_path in run_path.glob(glob):
#             file_name = file_path.name
#             out_name, domain, datetime = file_name.split('_', 2)
#             if (out_name, domain) in out_files:
#                 out_files[(out_name, domain)].append(str(file_path))
#                 out_files[(out_name, domain)].sort()
#             else:
#                 out_files[(out_name, domain)] = [str(file_path)]

#     return out_files


def select_files_to_ul(out_files, min_files):
    """

    """
    files = []
    for grp, file_paths in out_files.items():
        out_name, domain = grp
        n_files = len(file_paths)
        file_paths.sort(reverse=True)
        if out_name == 'wrfxtrm':
            files.extend(file_paths)
        elif n_files > min_files:
            files.extend(file_paths[min_files:n_files])

    return files


def rename_files(files, rename_dict):
    """

    """
    if rename_dict:
        new_files = set()
        for file_path in files:
            orig_path, orig_file_name = os.path.split(file_path)
            for orig, new in rename_dict.items():
                if orig in orig_file_name:
                    file_name = orig_file_name.replace(orig, new)
                    new_file_path = os.path.join(orig_path, file_name)
                    os.rename(file_path, new_file_path)
                    new_files.add(new_file_path)

        new_files = list(new_files)
    else:
        new_files = files

    return new_files


def resolve_output_variables(variables):
    """
    Expand user variable list with required coordinate/auxiliary variables.
    Always adds 2D coordinates. Adds 3D auxiliaries if any 3D variable is present.
    """
    var_set = set(variables)
    var_set.update(defaults.COORD_VARS_2D)
    if var_set & defaults.VARS_3D:
        var_set.update(defaults.COORD_VARS_3D)
    return sorted(var_set)


def filter_variables(files, variables):
    """

    """
    resolved = resolve_output_variables(variables)
    vars_str = ','.join(resolved)
    for file_path in files:
        orig_path, orig_file_name = os.path.split(file_path)
        if 'wrfout' in orig_file_name:
            cmd_str = f'ncks -O -4 -L 1 -v {vars_str} {orig_file_name} wrf_temp.nc'
            cmd_list = shlex.split(cmd_str)
            p = subprocess.run(cmd_list, capture_output=True, text=True, check=True, cwd=orig_path)
            os.replace(os.path.join(orig_path, 'wrf_temp.nc'), file_path)

    return True


def ul_output_files(files, run_path, name, out_path, config_path):
    """

    """
    files_str = '\n'.join([os.path.split(p)[-1] for p in files])
    print(f'-- Uploading files:\n{files_str}')

    cmd_str = f'rclone copy {run_path} {name}:{out_path} --transfers=4 --config={config_path} --files-from-raw -'
    cmd_list = shlex.split(cmd_str)

    start_ul = pendulum.now('UTC')
    p = subprocess.run(cmd_list, input=files_str, capture_output=True, text=True, check=False)
    end_ul = pendulum.now('UTC')

    diff = end_ul - start_ul

    mins = round(diff.total_minutes(), 1)

    if p.stderr == '':
        for file in files:
            if os.path.exists(file):
                os.remove(file)
        print(f'-- Upload successful in {mins} mins')


def recalc_geogrid(geogrid, domains):
    """

    """
    parent_ids = to_list(geogrid['parent_id'])
    old_max_domains = len(parent_ids)

    parent_grid_ratio = to_list(geogrid['parent_grid_ratio'])

    dx = geogrid['dx']
    dy = geogrid['dy']

    i_parent_start = to_list(geogrid['i_parent_start'])
    j_parent_start = to_list(geogrid['j_parent_start'])

    e_we = to_list(geogrid['e_we'])
    e_sn = to_list(geogrid['e_sn'])

    # define original projection
    map_proj = geogrid['map_proj'].lower()
    lat_0 = geogrid['ref_lat']
    lat_1 = geogrid['truelat1']
    lat_2 = geogrid['truelat2']

    if 'stand_lon' in geogrid:
        lon_0 = geogrid['stand_lon']
    else:
        lon_0 = geogrid['ref_lon']

    ref_lon = geogrid['ref_lon']

    new_top_domain = domains[0]

    # TODO: eventually I'd like to allow multiple sub domains below the ndown domain, but currently only one is allowed
    if new_top_domain > old_max_domains:
        raise ValueError('new_top_domain must be greater than max_domains')

    if new_top_domain > 1:

        lon_angle = lon_0 - ref_lon

        if map_proj == 'lambert':
            pwrf = f"""+proj=lcc +lat_1={lat_1} +lat_2={lat_2} +lat_0={lat_0} +lon_0={lon_0} +x_0=0 +y_0=0 +a={params.wrf_sphere_radius} +b={params.wrf_sphere_radius}"""
        elif map_proj == 'mercator':
            pwrf = f"""+proj=merc +lat_ts={lat_1} +lon_0={lon_0} +x_0=0 +y_0=0 +a={params.wrf_sphere_radius} +b={params.wrf_sphere_radius}"""
        elif map_proj == 'polar':
            pwrf = f"""+proj=stere +lat_ts={lat_1} +lat_0=90.0 +lon_0={lon_0} +x_0=0 +y_0=0 +a={params.wrf_sphere_radius} +b={params.wrf_sphere_radius}"""
        else:
            raise NotImplementedError('WRF proj not implemented yet: '
                                      f'{map_proj}')

        proj_crs = pyproj.CRS.from_string(pwrf)

        geo_crs = pyproj.CRS(
                proj='latlong',
                R=params.wrf_sphere_radius
            )

        geo_to_proj = pyproj.Transformer.from_crs(geo_crs, proj_crs, always_xy=True)
        proj_to_geo = pyproj.Transformer.from_crs(proj_crs, geo_crs, always_xy=True)

        index = new_top_domain - 1
        domain_seq = [index]
        while True:
            parent_id = parent_ids[index]
            if parent_id > 1:
                index = parent_id - 1
                domain_seq.insert(0, index)
            else:
                # domain_seq.insert(0, 0)
                break

        prev_x_center, prev_y_center = geo_to_proj.transform(ref_lon, lat_0)
        prev_dx_center = ((e_we[0] - 1) * 0.5) * dx
        prev_dy_center = ((e_sn[0] - 1) * 0.5) * dy
        for i in domain_seq:
            i_start = i_parent_start[i] - 1
            j_start = j_parent_start[i] - 1

            new_dx_start = i_start * dx
            new_dy_start = j_start * dy

            dx = dx / parent_grid_ratio[i]
            dy = dy / parent_grid_ratio[i]

            new_dx_end = new_dx_start + (dx * (e_we[i] - 1))
            new_dy_end = new_dy_start + (dy * (e_sn[i] - 1))

            new_dx_center = (new_dx_end + new_dx_start) * 0.5
            new_dy_center = (new_dy_end + new_dy_start) * 0.5

            ddx = new_dx_center - prev_dx_center
            ddy = new_dy_center - prev_dy_center

            new_x_center = prev_x_center + ddx
            new_y_center = prev_y_center + ddy

            ref_lon, lat_0 = proj_to_geo.transform(new_x_center, new_y_center)

            prev_x_center, prev_y_center = geo_to_proj.transform(ref_lon, lat_0)
            prev_dx_center = ((e_we[i] - 1) * 0.5) * dx
            prev_dy_center = ((e_sn[i] - 1) * 0.5) * dy

        lon_0 = ref_lon + lon_angle

    ## Save projection back to namelist.wps
    ref_lat = round(lat_0, 6)
    ref_lon = round(ref_lon, 6)
    stand_lon = round(lon_0, 6)

    geogrid['dx'] = int(dx)
    geogrid['dy'] = int(dy)
    geogrid['ref_lat'] = ref_lat
    geogrid['ref_lon'] = ref_lon
    geogrid['truelat1'] = ref_lat
    geogrid['truelat2'] = ref_lat
    geogrid['stand_lon'] = stand_lon

    ## Update other parameters in namelist.wps
    domain_index = [domain - 1 for domain in domains]
    new_top_parent_id = new_top_domain - 1
    geogrid['parent_id'] = [parent_ids[pid] - new_top_parent_id if parent_ids[pid] - new_top_parent_id > 1 else 1 for pid in domain_index]

    new_parent_grid_ratio = [parent_grid_ratio[index] for index in domain_index]
    new_parent_grid_ratio[0] = 1
    geogrid['parent_grid_ratio'] = new_parent_grid_ratio

    new_i_parent_start = [i_parent_start[index] for index in domain_index]
    new_i_parent_start[0] = 1
    geogrid['i_parent_start'] = new_i_parent_start

    new_j_parent_start = [j_parent_start[index] for index in domain_index]
    new_j_parent_start[0] = 1
    geogrid['j_parent_start'] = new_j_parent_start

    for p, v in geogrid.items():
        if isinstance(v, list):
            if len(v) == old_max_domains:
                geogrid[p] = [v[index] for index in domain_index]

    return geogrid


def update_geogrid(geogrid, domains):
    """

    """
    parent_ids = to_list(geogrid['parent_id'])
    old_max_domains = len(parent_ids)

    parent_grid_ratio = to_list(geogrid['parent_grid_ratio'])

    dx = geogrid['dx']
    dy = geogrid['dy']

    i_parent_start = to_list(geogrid['i_parent_start'])
    j_parent_start = to_list(geogrid['j_parent_start'])

    new_top_domain = domains[0]

    if new_top_domain > old_max_domains:
        raise ValueError('new_top_domain must be greater than max_domains')

    if new_top_domain > 1:

        index = new_top_domain - 1
        domain_seq = [index]
        while True:
            parent_id = parent_ids[index]
            if parent_id > 1:
                index = parent_id - 1
                domain_seq.insert(0, index)
            else:
                # domain_seq.insert(0, 0)
                break

        for i in domain_seq:
            dx = dx / parent_grid_ratio[i]
            dy = dy / parent_grid_ratio[i]

    geogrid['dx'] = int(dx)
    geogrid['dy'] = int(dy)

    ## Update other parameters in namelist.wps
    domain_index = [domain - 1 for domain in domains]
    new_top_parent_id = new_top_domain - 1
    geogrid['parent_id'] = [parent_ids[pid] - new_top_parent_id if parent_ids[pid] - new_top_parent_id > 1 else 1 for pid in domain_index]

    new_parent_grid_ratio = [parent_grid_ratio[index] for index in domain_index]
    new_parent_grid_ratio[0] = 1
    geogrid['parent_grid_ratio'] = new_parent_grid_ratio

    new_i_parent_start = [i_parent_start[index] for index in domain_index]
    new_i_parent_start[0] = 1
    geogrid['i_parent_start'] = new_i_parent_start

    new_j_parent_start = [j_parent_start[index] for index in domain_index]
    new_j_parent_start[0] = 1
    geogrid['j_parent_start'] = new_j_parent_start

    for p, v in geogrid.items():
        if isinstance(v, list):
            if len(v) == old_max_domains:
                geogrid[p] = [v[index] for index in domain_index]

    return geogrid



















