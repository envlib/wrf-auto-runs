#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 10:40:23 2025

@author: mike
"""
import os
import pathlib
import resource
import shlex
import subprocess
import pendulum
import sentry_sdk

import params



############################################
### Parameters


###########################################
### Functions


_ATTACHED = set()   # metgrid logs already attached to the Sentry scope (a retried chunk calls run_metgrid again)


def metgrid_logs():
    """metgrid's log files: metgrid.log with one rank, metgrid.log.0000 ... with more (WPS module_debug.F)."""
    return sorted(params.data_path.glob('metgrid.log*'))


def metgrid_log_report(n_lines=30):
    """The last n_lines of each metgrid log holding 'ERROR' (else of the first log), as one printable block, or '' when
    there is no log. (A retried chunk deletes the logs between attempts, so these are always the current attempt's.)
    """
    logs = metgrid_logs()
    if not logs:
        return ''
    texts = {f: f.read_text(errors='replace') for f in logs}
    chosen = [f for f in logs if 'ERROR' in texts[f]] or logs[:1]
    out = []
    for f in chosen:
        tail = texts[f].rstrip().splitlines()[-n_lines:]
        out.append(f'---- {f.name} (last {len(tail)} lines) ----\n' + '\n'.join(tail))
    return '\n'.join(out)


def run_metgrid(del_old=True):
    """

    """
    resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

    os.environ['OMP_STACKSIZE'] = '2G'
    os.environ['KMP_STACKSIZE'] = '2G'
    
    cmd_str = f'mpirun -n {params.n_cores_metgrid} {params.metgrid_exe}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False, cwd=params.data_path)

    if 'Successful completion of metgrid.' in p.stdout:
        if del_old:
            for path in params.data_path.glob('ERA5:*'):
                path.unlink()
            for path in params.data_path.glob('WRF:*'):
                path.unlink()
            for path in params.data_path.glob('SST:*'):
                path.unlink()
            if params.input_prefix:
                for path in params.data_path.glob(f'{params.input_prefix}:*'):
                    path.unlink()
        return True
    else:
        # Print the logs' tails here: on a recovered retry chunk.sl deletes scratch, so the job log is the only record.
        report = metgrid_log_report()
        print(f'-- metgrid failed (exit {p.returncode}).\n{report or "(no metgrid.log* found)"}')
        if params.is_sentry:
            scope = sentry_sdk.get_current_scope()
            for f in metgrid_logs():
                if f not in _ATTACHED:
                    scope.add_attachment(path=f)
                    _ATTACHED.add(f)
        raise ValueError(f'metgrid failed (exit {p.returncode}); see the metgrid.log* tails above. stderr: {p.stderr}')




