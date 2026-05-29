#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Oct 23 15:28:14 2025

@author: mike
"""
import copy

import params
import utils


############################################################
### Checks


def check_ndown_params(domains):
    """
    Validate [ndown] config + run domains and return execution metadata.

    Modes
    -----
    "single" (existing): run = [N] with N != 1.
        ndown produces wrfinput_dN / wrfbdy_dN from a previously-run parent
        wrfout downloaded from [ndown.input]. wrf.exe then runs domain N
        standalone.

    "nested-run" (new): run = [N, M, ...] with N != 1 and each subsequent
        domain's parent_id earlier in the list.
        ndown produces the first element from its parent (downloaded). After
        ndown, wrf.exe runs N + the listed nested children in a single
        invocation using two-way nesting — the inner BCs come from the in-run
        simulation, not a second ndown call.

    Returns
    -------
    ndown_check : bool
        True if ndown will be invoked.
    ndown_mode : str
        "single" or "nested-run".
    nested_run_domains : list[int] | None
        User-numbered domains for the post-ndown nested wrf.exe run. None when
        ndown_check is False or mode is "single".
    domains_init : list[int]
        Domains that geogrid/metgrid/real need to produce all required inputs.
        For "single" mode: [ndown_parent, ndown_target] (existing behaviour).
        For "nested-run" mode: union of ndown_parent and every domain in run,
        sorted ascending.
    """
    ndown_check = False
    ndown_mode = "single"
    nested_run_domains = None
    domains_init = copy.deepcopy(domains)

    if 'ndown' in params.file:
        ndown = params.file['ndown']

        if ndown.get('input', {}).get('path'):
            if not domains:
                raise ValueError(
                    "If ndown inputs are passed, [domains].run must list at least "
                    "one target domain (a non-domain-1 integer)."
                )
            if domains[0] == 1:
                raise ValueError(
                    "ndown target (run[0]) cannot be domain 1 — ndown produces "
                    "a child from a parent's wrfout, so the target must be a nest."
                )

            domain_config = params.file['domains']
            parent_ids = utils.to_list(domain_config['parent_id'])

            # Multi-element run requires each subsequent child's parent to be
            # already present earlier in the list (so the nest chain is rooted
            # at the ndown target and is fully self-contained).
            for i, d in enumerate(domains[1:], start=1):
                parent = parent_ids[d - 1]
                if parent not in domains[:i]:
                    raise ValueError(
                        f"In multi-domain ndown mode, domain {d}'s parent ({parent}) "
                        f"must appear earlier in run. Got run={domains}."
                    )

            ndown_check = True
            ndown_target = domains[0]
            ndown_parent = parent_ids[ndown_target - 1]

            if len(domains) == 1:
                # Existing single-ndown behaviour.
                domains_init = [ndown_parent, ndown_target]
            else:
                # New nested-run mode: geogrid/metgrid/real need all the post-ndown
                # nested children too, so real.exe can produce wrfinput_d0N for each.
                ndown_mode = "nested-run"
                domains_init = sorted(set([ndown_parent, *domains]))
                nested_run_domains = list(domains)

    return ndown_check, ndown_mode, nested_run_domains, domains_init
