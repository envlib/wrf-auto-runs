# gfortran_wvt_ref — the frozen reference image

`pipeline/` is a vendored, FROZEN copy of the pipeline (see the Dockerfile): it is not rebuilt against later
fixes, on purpose.

⚠ **Nudging window (2026-09-30).** This copy still derives `gfdda_end_h` as `end_date − start_date`
(`pipeline/set_params.py:356-358`). WRF counts that from the simulation start, which is `begin_hours` earlier,
so with a spin-up nudging stops `begin_hours` before the run ends. The live pipeline (image 2.14 / avx512 1.7)
fixed this; this copy was deliberately left frozen (ruled 2026-09-30). **Any nudged run on this image with a
spin-up must pin `gfdda_end_h` = spin-up + output hours** in its `[fdda]`, e.g. P1's `a_ref_north`
(672 h + 144 h): `gfdda_end_h = [816, 0]`. This copy has no chunked/restart mode, so the chunk half of the bug
does not apply. (A wrf-runs config guard that refuses such a config without the pin is on the C1 re-run TO DO in
wrf-model-eval OPEN_WORK -- until it exists, the pin is on you.)
