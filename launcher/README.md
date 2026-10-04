# launcher — submit a WRF restart chain to any Slurm cluster

One `submit` script and one generic `chunk.sl` for every cluster. A project dir holds only its configuration;
cluster specifics live in a site file you keep outside this repo.

```
submit --cluster <name> --site <site.toml> <project_dir> [--dry-run] [--uuid U] [--keep-scratch] [--max-jobs N]
```

Run it on the login node. It needs only bash ≥ 4.4, awk, GNU date, `sbatch`, `squeue` and `scancel`; no Python.

## What goes where

| file | holds | example |
|---|---|---|
| `<project>/parameters.toml` | the pipeline's config, with `[restart] enable = true`, `stop_after_upload = true`, `interval_days`, and `[remote.output] path` set | `../parameters_example.toml` (those keys are commented out there) |
| `<project>/launcher.toml` | image, extra sbatch args, spares, extra container env | below |
| site file (private) | per cluster: all sbatch resource options, `shared_base`, `scratch_base`, `module_load` | `site_example.toml` |

```toml
# launcher.toml -- only these keys are accepted
[launcher]
image_name    = "wrf-auto-runs-intel-wvt-avx512"   # required
image_version = "1.7"                              # required
sbatch_args   = "--time=36:00:00"                  # optional; appended after the site's
spares        = 1                                  # optional, default 1: extra jobs that find the run done and exit

[env]                                              # optional; each key -> one `apptainer --env KEY=VALUE`
WVT_TRMASK_2D = "1"
```

`[env]` refuses names the launcher or the pipeline config already set (`run_uuid`, the dates, `restart_*`,
`n_cores`, `n_cores_preprocess`, the `RCLONE_*` stall settings, …): a second value would desynchronise the chain.
Values must fit on one line and contain no `,`, because Apptainer splits `--env` on commas.

## What `submit` does

1. **Validates everything before the first `sbatch`.** It refuses a chain that would queue and then do nothing
   useful:
   - `[restart] enable` or `stop_after_upload` not `true`;
   - `preprocess_only = true`;
   - no `[remote.output] path` (every job would cold-start chunk 1);
   - an active credential still `<<<SET-BEFORE-RUN>>>`, or a `<<<placeholder>>>` in the site file;
   - no `--ntasks` in the sbatch args;
   - bad dates or a non-integer `begin_hours`.
2. **Picks the run_uuid:** `--uuid`, else the top-level `run_uuid`. It is never generated, because re-running the
   same command is how a chain resumes.
3. **Counts the jobs:** `N = ceil((end − start + begin_hours) / interval_days) + spares`. WRF starts
   `begin_hours` before `start_date`, and `end` may come from `duration_hours` (`end_date` wins when both are set,
   as in the pipeline). More than 60 jobs is refused unless you pass `--max-jobs N`, so a mistyped `end_date`
   cannot queue hundreds of jobs.
4. **Refuses a uuid already queued or running** for you on this cluster (job name `wrf-<uuid>`). It cannot see
   other clusters: queue a run on one cluster only.
5. **Writes a snapshot** `<project>/runs/<UTC time>-<cluster>/`, holding both tomls, a copy of `chunk.sl` and
   `job.env` (mode 600; the snapshot copies `parameters.toml`, credentials included). Every job reads the snapshot,
   so editing the project or updating this checkout never changes a chain that is already queued. Each submission
   makes a new dir; old ones are a record. Do not delete `runs/` while a chain is queued.
6. **Submits N copies of `chunk.sl`**: job 1 at once, each later job `afterany` on the one before. If an `sbatch`
   fails, or `submit` is interrupted (Ctrl-C, kill, a dropped ssh session), it cancels the chain: by id, newest
   first, then by job name. The by-name cancel also catches a job accepted in the moment the signal arrived. If
   `scancel` itself fails, it says so.

`--dry-run` prints the uuid, N, `job.env` and every `sbatch` command, and touches nothing. It also prints an
`sbatch --test-only …` line you can paste to have Slurm itself check the options without running anything.

## What each job does (`chunk.sl`)

It reads `$1/job.env` and loads the Apptainer module if needed. It then runs the pipeline in the SIF with the
standard binds (`parameters.toml`, `WPS_GEOG` read-only, scratch as `/data`, `/dev/shm`, and scratch
`/tmp` — required, or ERA5 downloads truncate silently). Scratch is kept when the job fails, when an upload failed
(the local wrfout is then the only copy), or with `--keep-scratch`. A job whose pipeline succeeded but whose upload
failed exits **75**, so `sacct` and mail-on-fail show it; the chain carries on, because the restart is a separate
upload.

## Hooks (optional)

A project dir may hold a `hooks/` folder with `pre` and/or `post` (bash scripts) and any files they use. `submit`
snapshots the folder with everything else. `chunk.sl` runs `hooks/pre` before the pipeline, and a non-zero exit
stops the job. It runs `hooks/post` after the pipeline, and a non-zero exit only prints a WARNING: the job still
exits with the pipeline's status.

Hooks run on the host with apptainer on PATH. These variables are exported: `RUN_DIR RUN_UUID CLUSTER SIF_PATH
IMAGE_NAME IMAGE_VERSION PARAMS_FILE LOCAL_SCRATCH SLURM_JOB_ID`, and `post` also gets `CHUNK_RC`. `submit` refuses
an empty hook, and a `hooks/` folder that holds neither `pre` nor `post` (a misnamed hook would otherwise never run).
Example: C1 runs a config guard and its keep-every-restart archive this way
(`wrf-runs/projects/wvt/c1_launcher/hooks/`).

## Caveats

- **sbatch options:** the project's `sbatch_args` follow the site's, so the last value wins for most options. But
  `--exclusive` cannot be undone, and giving both `--mem` and `--mem-per-cpu` is a Slurm error.
- **Login-shell variables:** `submit` unsets `SBATCH_*` before calling `sbatch`, so they cannot override the site
  file. `APPTAINERENV_*` still reaches the container.
- **TOML subset:** values are read by a small awk parser, so keep them flat, one per line. A `#` inside a quoted
  value is cut off.
- **Resubmitting** snapshots the *current* tomls. A changed `parameters.toml` continues from the existing restart
  of the same uuid; change the uuid to start afresh.
- **The SIF is read by path:** re-pulling the same tag mid-chain changes what later jobs run.
- **Only the newest restart is kept.** The pipeline prunes older `wrfrst` files under `inputs/<run_uuid>/`. A
  campaign that needs every restart (C1) adds its own archive step.
- **A chain can end short with every job green.** If the pipeline's restart listing fails, that job cold-starts
  chunk 1 and uses up a spare (a pipeline behaviour, tracked separately). Check the last job's log reaches the end
  date; re-running the same command resumes.

## Self-test

```
python3 launcher/test_launcher.py
```

It needs only the standard library, and uses fake `sbatch`/`squeue`/`scancel`/`apptainer`/`module` with
synthetic fixtures. Each case names the defect it exists to catch.
