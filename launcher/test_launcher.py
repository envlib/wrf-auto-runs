#!/usr/bin/env python3
"""Self-test for launcher/submit and launcher/chunk.sl, with fake sbatch/squeue/scancel/apptainer/module.

    python3 launcher/test_launcher.py            # stdlib only; bash >= 4.4 and GNU date on PATH
    SUBMIT=/path/to/mutant python3 launcher/test_launcher.py

Every fixture is synthetic and built in a temp dir (the repo's .gitignore ignores parameters.toml). Each case
names, in brackets, the defect it exists to catch. Exit 0 only if every case passes.
"""
import datetime
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
SUBMIT = pathlib.Path(os.environ.get('SUBMIT', HERE / 'submit'))
CHUNK = pathlib.Path(os.environ.get('CHUNK', HERE / 'chunk.sl'))
PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond or not detail else f'\n      {str(detail)[-600:]}'))


FAKE_SBATCH = r'''#!/usr/bin/env python3
import json, os, shutil, sys, time
log = os.environ['FAKE_LOG']
n = sum(1 for _ in open(log)) + 1 if os.path.exists(log) else 1
time.sleep(float(os.environ.get('FAKE_SLEEP', '0')))
fail_at = os.environ.get('FAKE_FAIL_AT')
if fail_at and n >= int(fail_at):
    sys.stderr.write('sbatch: error: fake failure\n'); sys.exit(1)
args = sys.argv[1:]
opts, rest = [], []
for i, a in enumerate(args):                 # real sbatch stops option parsing at the script path
    if not a.startswith('-'):
        rest = args[i:]; break
    opts.append(a)
spool = os.environ.get('FAKE_SPOOL')
if spool and rest:                           # slurmctld keeps its own copy of the script
    os.makedirs(spool, exist_ok=True); shutil.copy(rest[0], os.path.join(spool, f'job{1000 + n}.sl'))
with open(log, 'a') as f:
    um = os.umask(0); os.umask(um)
    f.write(json.dumps({'id': 1000 + n, 'opts': opts, 'rest': rest, 'umask': um,
                        'sbatch_env': sorted(k for k in os.environ if k.startswith('SBATCH_'))}) + '\n')
if os.environ.get('FAKE_GARBAGE_AT') == str(n):
    print('Submitted batch job'); sys.exit(0)          # accepted by the controller, but no id on stdout
print(f'{1000 + n};cl' if n == 1 and os.environ.get('FAKE_SUFFIX') else 1000 + n)
'''
FAKE_SQUEUE = r'''#!/usr/bin/env python3
import os, sys
if os.environ.get('FAKE_SQ_LOG'): open(os.environ['FAKE_SQ_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\n')
if os.environ.get('SQUEUE_FAIL'): sys.stderr.write('squeue: error\n'); sys.exit(1)
a = sys.argv[1:]
if '-h' not in a: print('JOBID')                     # real squeue prints a header unless -h
user = a[a.index('-u') + 1] if '-u' in a else None
name = a[a.index('-n') + 1] if '-n' in a else None
owner = os.environ.get('QUEUED_USER') or os.environ.get('USER') or __import__('pwd').getpwuid(os.getuid()).pw_name   # who owns the job
if name and name == os.environ.get('QUEUED_NAME') and (user is None or user == owner):   # no -u lists ALL users
    print(777)
'''
FAKE_SCANCEL = r'''#!/usr/bin/env python3
import os, sys
open(os.environ['FAKE_CANCEL'], 'a').write(' '.join(sys.argv[1:]) + '\n')
sys.exit(1 if os.environ.get('FAKE_SCANCEL_FAIL') else 0)
'''
FAKE_APPTAINER = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
for i, x in enumerate(a):                    # apptainer's --env is key=value; an empty value is a hard error
    if x == '--env' and '=' not in a[i + 1]:
        sys.stderr.write(f'Error: invalid argument "{a[i + 1]}" for "--env" flag\n'); sys.exit(2)
open(os.environ['FAKE_APPT'], 'a').write(json.dumps(a) + '\n')
open(os.environ['FAKE_APPT'] + '.env', 'w').write(json.dumps({k: os.environ.get(k) for k in ('APPTAINER_CACHEDIR', 'APPTAINER_TMPDIR')}))
sys.stdout.write(os.environ.get('FAKE_APPT_OUT', '') + '\n')
sys.stderr.write('pipeline-stderr-marker\n')
sys.exit(int(os.environ.get('FAKE_APPT_RC', '0')))
'''
FAKE_MODULE = r'''#!/bin/bash
echo "$*" >> "$FAKE_MODULE_LOG"
'''

SITE = '''# synthetic site file
[hetzner]
sbatch_args  = "--partition=batch --time=24:00:00 --ntasks=48 --mem=0 --exclude=n[01-03,07]"
shared_base  = "{shared}"
scratch_base = "{scratch}"
module_load  = ""

[nesi]
sbatch_args  = "--account=nesi99999 --ntasks=48 --mem=64G"   # trailing comment
shared_base  = "{shared}"
scratch_base = "{scratch}"
module_load  = "Apptainer"

[nontasks]
sbatch_args  = "--partition=batch --mem=0"
shared_base  = "{shared}"
scratch_base = "{scratch}"

[dollar]
sbatch_args  = "--ntasks=48"
shared_base  = "/scratch/jobs/${{USER}}"
scratch_base = "{scratch}"

[noscratch]
sbatch_args  = "--ntasks=48"
shared_base  = "{shared}"

[placeholder]
sbatch_args  = "--ntasks=48"
shared_base  = "/nesi/nobackup/x/<<<USERNAME>>>"
scratch_base = "{scratch}"

[shortn]
sbatch_args  = "--partition=batch -n 48"
shared_base  = "{shared}"
scratch_base = "{scratch}"

[percore]
sbatch_args  = "--partition=batch --ntasks-per-core=1"
shared_base  = "{shared}"
scratch_base = "{scratch}"

[trailing]
sbatch_args  = "--ntasks=48"	
shared_base  = "{shared}"   	
scratch_base = "{scratch}"  # comment
'''

PARAMS = '''run_uuid = "{uuid}"
{top}
[time_control]
start_date = "{start}"
{endline}

[time_control.history_file]
begin_hours = {begin}

[restart]
enable = {enable}
interval_days = {interval}
stop_after_upload = {stop}

[remote.output]
type = "s3"
access_key_id = "{ak}"
secret_access_key = "{sk}"
{path}
'''

LAUNCH = '''[launcher]
image_name = "wrf-auto-runs-test"
image_version = "9.9"
{extra}
{env}
'''


class Env:
    def __init__(self, tmp):
        self.tmp = tmp
        self.bin = tmp / 'bin'
        self.bin.mkdir()
        for name, body in (('sbatch', FAKE_SBATCH), ('squeue', FAKE_SQUEUE), ('scancel', FAKE_SCANCEL),
                           ('apptainer', FAKE_APPTAINER)):
            (self.bin / name).write_text(body.replace('#!/usr/bin/env python3', f'#!{sys.executable}', 1))
            (self.bin / name).chmod(0o755)
        self.modbin = tmp / 'modbin'
        self.modbin.mkdir()
        (self.modbin / 'module').write_text(FAKE_MODULE)
        (self.modbin / 'module').chmod(0o755)
        self.shared = tmp / 'shared'
        (self.shared / 'WPS_GEOG').mkdir(parents=True)
        (self.shared / 'wrf-auto-runs-test_9.9.sif').write_text('sif')
        self.scratch = tmp / 'scratch'
        self.site = tmp / 'site.toml'
        self.site.write_text(SITE.format(shared=self.shared, scratch=self.scratch))
        self.log, self.cancel, self.appt = tmp / 'sbatch.log', tmp / 'cancel.log', tmp / 'appt.log'
        self.n = 0

    def project(self, uuid='t_run', start='2021-07-01 00:00:00', end='2022-07-01 00:00:00', dur=None,
                begin=672, enable='true', interval=28, stop='true', path='bkt/out', ak='AKIA0', sk='x', top='',
                extra='spares = 3', env='', name=None):
        self.n += 1
        p = self.tmp / (name or f'proj{self.n}')
        p.mkdir()
        endline = f'end_date = "{end}"' if end is not None else ''
        if dur is not None:
            endline += f'\nduration_hours = {dur}'
        (p / 'parameters.toml').write_text(PARAMS.format(
            uuid=uuid, top=top, start=start, endline=endline, begin=begin, enable=enable, interval=interval,
            stop=stop, ak=ak, sk=sk, path=f'path = "{path}"' if path else ''))
        (p / 'launcher.toml').write_text(LAUNCH.format(extra=extra, env=env))
        return p

    def env(self, now, extra_env=None):
        for f in (self.log, self.cancel):
            if f.exists():
                f.unlink()
        e = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', FAKE_LOG=str(self.log),
                 FAKE_CANCEL=str(self.cancel), LAUNCHER_NOW=now)
        e.update(extra_env or {})
        return e

    def submit(self, proj, *args, cluster='hetzner', extra_env=None, cwd=None, now='20261004T000000Z', umask=None):
        e = self.env(now, extra_env)
        pre = (lambda: os.umask(umask)) if umask is not None else None
        pr = subprocess.run(['bash', str(SUBMIT), '--cluster', cluster, '--site', str(self.site), str(proj), *args],
                            capture_output=True, text=True, env=e, cwd=cwd or self.tmp, preexec_fn=pre)
        calls = [json.loads(ln) for ln in self.log.read_text().splitlines()] if self.log.exists() else []
        return pr.returncode, pr.stdout + pr.stderr, calls


def njobs(out):
    m = re.search(r'jobs:\s+(\d+) = (\d+) chunks', out)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def pipeline_chunks(start, end, begin_h, interval_d):
    """The pipeline's loop: from start - begin_hours, one chunk per interval_days until the end (naive times)."""
    cur, n = start - datetime.timedelta(hours=begin_h), 0
    while cur < end:
        cur += datetime.timedelta(days=interval_d)
        n += 1
    return n


def count_cases(E):
    p = E.project()
    rc, out, cl = E.submit(p, '--dry-run')
    check('count: C1-shaped year (393 d incl. 672 h spin-up, 28 d, 3 spares) -> 18 jobs [spin-up dropped / floor]',
          rc == 0 and njobs(out) == (18, 15), out[-300:])
    p = E.project(start='2021-01-01 00:00:00', end='2021-02-26 00:00:00', begin=0, extra='')
    rc, out, _ = E.submit(p, '--dry-run')
    check('count: exact multiple (56 d / 28) -> 2 chunks, + default 1 spare [a "+1" / default spares]',
          njobs(out) == (3, 2), out[-200:])
    p = E.project(start='2021-03-15 00:00:00', end='2021-05-10 00:00:00', begin=0, extra='spares = 0')
    rc, out, _ = E.submit(p, '--dry-run', extra_env={'TZ': 'Pacific/Auckland'})
    check('count: 56 d across the NZ DST change under TZ=Pacific/Auckland -> 2 chunks, spares=0 honoured [date without -u]',
          njobs(out) == (2, 2), out[-200:])
    p = E.project(end=None, dur=24 * 400, begin=24, extra='spares = 0')
    rc, out, _ = E.submit(p, '--dry-run')
    check('count: duration_hours instead of end_date -> ceil((9600 + 24) h / 672 h) = 15 [duration_hours ignored]',
          njobs(out) == (15, 15), out[-200:])
    rng = random.Random(7)
    bad = []
    for _ in range(40):
        s = datetime.datetime(2000, 1, 1) + datetime.timedelta(hours=rng.randrange(0, 200000))
        e = s + datetime.timedelta(hours=rng.randrange(1, 20000))
        b, iv = rng.choice([0, 6, 24, 672]), rng.choice([1, 7, 28, 30])
        p = E.project(start=f'{s:%Y-%m-%d %H:%M:%S}', end=f'{e:%Y-%m-%d %H:%M:%S}', begin=b, interval=iv, extra='spares = 0')
        _, out, _ = E.submit(p, '--dry-run', '--max-jobs', '100000', extra_env={'TZ': 'Pacific/Auckland'})
        if njobs(out)[1] != pipeline_chunks(s, e, b, iv):
            bad.append((s, e, b, iv, njobs(out)))
    check('count: 40 random windows match the pipeline loop, under TZ=Pacific/Auckland [any formula drift]', not bad, bad[:3])


def chain_cases(E):
    (E.tmp / '--exclude=n0').write_text('')             # the file an unquoted --exclude=n[01-03,07] would glob to
    p = E.project(extra='spares = 1')
    rc, out, cl = E.submit(p, extra_env={'FAKE_SUFFIX': '1'})
    ids = [c['id'] for c in cl]
    deps = [[o for o in c['opts'] if o.startswith('--dependency')] for c in cl]
    ok = rc == 0 and len(cl) == 16 and deps[0] == [] and all(deps[i] == [f'--dependency=afterany:{ids[i - 1]}']
                                                              for i in range(1, len(cl)))
    check('chain: job 1 has no dependency; job i is afterany on job i-1, with "id;cl" stripped [afterok / wrong id]',
          ok, (out[-300:], deps[:3]))
    run_dir = re.search(r'snapshot:\s+(\S+)', out).group(1)
    check('chain: every job runs the SNAPSHOT chunk.sl with the snapshot dir as $1, --chdir into it [project/checkout copy]',
          all(c['rest'] == [f'{run_dir}/chunk.sl', run_dir] and f'--chdir={run_dir}' in c['opts'] for c in cl), cl[0])
    check('chain: job name wrf-<uuid> on every job', all('--job-name=wrf-t_run' in c['opts'] for c in cl))
    o = cl[0]['opts']
    check('argv: site args in order, then launcher-owned flags; glob arg n[01-03,07] intact [unquoted expansion]',
          o[:6] == ['--parsable', '--partition=batch', '--time=24:00:00', '--ntasks=48', '--mem=0',
                    '--exclude=n[01-03,07]'], o)


def argv_cases(E):
    p = E.project(extra='spares = 0\nsbatch_args = "--time=36:00:00"')
    (E.tmp / 'n0').write_text('')                        # a file a glob would match
    rc, out, cl = E.submit(p, extra_env={'SBATCH_TIMELIMIT': '1:00:00', 'SBATCH_PARTITION': 'x'})
    o = cl[0]['opts'] if cl else []
    check('argv: project --time comes AFTER the site --time; launcher flags last [override order]',
          rc == 0 and o.index('--time=36:00:00') > o.index('--time=24:00:00') and o[-1].startswith('--chdir='), o)
    check('argv: SBATCH_* from the login shell is unset before sbatch [silent header override]',
          cl and all(c['sbatch_env'] == [] for c in cl), cl[0]['sbatch_env'] if cl else out)
    p = E.project(extra='spares = 0')
    _, dry, _ = E.submit(p, '--dry-run')
    rc, out, cl = E.submit(p, now='20261004T000001Z')
    dry_lines = [ln.strip() for ln in dry.splitlines() if ln.strip().startswith('sbatch ') and '--test-only' not in ln]
    real = []
    for c in cl:
        argv = [re.sub(r'afterany:\d+', 'afterany:X', a) for a in c['opts'] + c['rest']]
        real.append(argv)
    dryv = [[re.sub(r'afterany:<job\d+>', 'afterany:X', a) for a in subprocess.run(
        ['bash', '-c', f'eval "set -- {ln[7:]}"; printf "%s\\n" "$@"'], capture_output=True, text=True).stdout.splitlines()]
        for ln in dry_lines]
    dryv = [[re.sub(r'20261004T000000Z', '20261004T000001Z', a) for a in v] for v in dryv]
    check('dry run: printed argv == the real argv, job by job [dry run lies]', rc == 0 and dryv == real,
          (dryv[:1], real[:1]))


def queue_cases(E):
    p = E.project()
    rc, out, cl = E.submit(p, extra_env={'QUEUED_NAME': 'wrf-t_run'})
    check('queue: same uuid queued -> refused, zero sbatch, NO snapshot dir [double chain]',
          rc == 1 and not cl and not (p / 'runs').exists() and 'already queued' in out, out[-200:])
    rc, out, cl = E.submit(p, extra_env={'QUEUED_NAME': 'wrf-other'})
    check('queue: another uuid queued -> allowed', rc == 0 and len(cl) == 18, out[-200:])
    p = E.project()
    rc, out, cl = E.submit(p, extra_env={'SQUEUE_FAIL': '1'})
    check('queue: squeue fails -> refused, zero sbatch [error read as "nothing queued"]', rc != 0 and not cl, out[-200:])
    p = E.project()
    rc, out, cl = E.submit(p, extra_env={'QUEUED_NAME': 'wrf-t_run', 'QUEUED_USER': 'someone_else'})
    check('queue: the same uuid queued by ANOTHER user does not block [-u filter dropped would refuse]',
          rc == 0 and len(cl) == 18, out[-200:])
    p = E.project()
    pr_env = {k: v for k, v in os.environ.items() if k != 'USER'}
    e2 = dict(pr_env, PATH=f'{E.bin}:{os.environ["PATH"]}', FAKE_LOG=str(E.log), FAKE_CANCEL=str(E.cancel),
              LAUNCHER_NOW='20261004T000009Z')
    if E.log.exists():
        E.log.unlink()
    pr = subprocess.run(['bash', str(SUBMIT), '--cluster', 'hetzner', '--site', str(E.site), str(p)],
                        capture_output=True, text=True, env=e2)
    check('queue: works with USER unset (id -un)', pr.returncode == 0, pr.stderr[-200:])


def uuid_cases(E):
    p = E.project(uuid='from_toml', extra='spares = 0')
    rc, out, cl = E.submit(p, '--uuid', 'from_flag', extra_env={'RUN_UUID': 'stale_env'})
    env_txt = (pathlib.Path(re.search(r'snapshot:\s+(\S+)', out).group(1)) / 'job.env').read_text() if rc == 0 else ''
    check('uuid: --uuid beats the toml; a stale RUN_UUID in the shell is ignored; source printed [stale env wins]',
          rc == 0 and all('--job-name=wrf-from_flag' in c['opts'] for c in cl) and 'RUN_UUID=from_flag' in env_txt
          and '(from --uuid)' in out, out[-300:])
    p = E.project(uuid='from_toml', extra='spares = 0')
    rc, out, cl = E.submit(p, extra_env={'RUN_UUID': 'stale_env'})
    check('uuid: without --uuid the toml wins over a stale RUN_UUID', rc == 0 and '--job-name=wrf-from_toml' in cl[0]['opts'],
          out[-200:])
    for u, label in (('', 'empty run_uuid'), ('..', '".."'), ('a b', 'a space'), ('-x', 'a leading "-"')):
        p = E.project(uuid=u)
        rc, out, cl = E.submit(p)
        check(f'uuid: {label} -> refused, zero sbatch', rc == 1 and not cl, out[-150:])


def snapshot_cases(E):
    p = E.project(extra='spares = 0\n', env='[env]\nWVT_TRMASK_2D = "1"')
    spool = E.tmp / 'spool'
    (E.tmp / 'sub').mkdir()
    rel = os.path.relpath(p, E.tmp / 'sub')
    rc, out, cl = E.submit(rel, extra_env={'FAKE_SPOOL': str(spool)}, cwd=E.tmp / 'sub')
    run_dir = pathlib.Path(re.search(r'snapshot:\s+(\S+)', out).group(1)) if rc == 0 else None
    check('snapshot: a RELATIVE project dir and no runs/ yet -> absolute snapshot dir created [relative $1 / mkdir]',
          rc == 0 and run_dir.is_absolute() and run_dir.is_dir(), out[-300:])
    if rc != 0:
        return
    mode = (run_dir / 'parameters.toml').stat().st_mode & 0o077
    check('snapshot: the parameters.toml copy is not group/world readable [credentials exposed]', mode == 0, oct(mode))
    # Edit everything outside the snapshot, then run the spooled job as slurmctld would.
    (p / 'parameters.toml').write_text('broken')
    (p / 'launcher.toml').write_text('broken')
    job = sorted(spool.glob('job*.sl'))[0]
    res = run_job(E, job, run_dir, cwd=run_dir)
    appt = json.loads(E.appt.read_text().splitlines()[-1]) if E.appt.exists() else []
    bind = appt[appt.index('--bind') + 1] if '--bind' in appt else ''
    check('snapshot: after editing the project, the spooled job binds the SNAPSHOT parameters.toml [project read live]',
          res.returncode == 0 and bind.startswith(f'{run_dir}/parameters.toml:/app/parameters.toml'), (res.stdout[-300:], bind))
    rc2, out2, _ = E.submit(E.project(), now='20261004T000100Z')
    rc3, out3, _ = E.submit(pathlib.Path(re.search(r'project:\s+(\S+)', out2).group(1)), now='20261004T000200Z')
    check('snapshot: a second submit of a project makes a NEW dir, the first untouched',
          rc2 == 0 and rc3 == 0 and re.search(r'snapshot:\s+(\S+)', out2).group(1) != re.search(r'snapshot:\s+(\S+)', out3).group(1))
    p = E.project()
    E.submit(p, now='20261004T000300Z')
    rc, out, cl = E.submit(p, now='20261004T000300Z')
    check('snapshot: same-second collision -> refused before any sbatch, first dir intact', rc == 1 and not cl, out[-200:])
    rc, out, cl = E.submit(p, '--dry-run', now='20261004T000400Z')
    check('dry run: writes nothing, calls no sbatch', rc == 0 and not cl and not (p / 'runs' / '20261004T000400Z-hetzner').exists())


def tools_without_apptainer(E):
    """A PATH dir of symlinks to every tool on the real PATH except apptainer/module, so a job can be run on a host
    that has a real apptainer in /usr/bin (filtering that whole dir off PATH would also drop bash)."""
    d = E.tmp / 'tools'
    if not d.exists():
        d.mkdir()
        for p in os.environ['PATH'].split(':'):
            if not os.path.isdir(p):
                continue
            for f in os.listdir(p):
                if f in ('apptainer', 'singularity', 'module') or (d / f).exists():
                    continue
                src = os.path.join(p, f)
                if os.path.isfile(src) and os.access(src, os.X_OK):
                    (d / f).symlink_to(src)
    return d


def run_job(E, script, run_dir, cwd, rc=0, out='', extra_env=None, path_bins=None, arg=True):
    if E.appt.exists():
        E.appt.unlink()
    bins = path_bins if path_bins is not None else [E.bin]
    e = dict(os.environ, PATH=':'.join(map(str, bins)) + ':' + str(tools_without_apptainer(E)), SLURM_JOB_ID='4242', SLURM_NTASKS='48',
             FAKE_APPT=str(E.appt), FAKE_APPT_RC=str(rc), FAKE_APPT_OUT=out, FAKE_MODULE_LOG=str(E.tmp / 'module.log'))
    e.update(extra_env or {})
    os.chmod(script, 0o700)                         # run through its own shebang (#!/bin/bash -e), as slurmd does
    return subprocess.run([str(script)] + ([str(run_dir)] if arg else []), capture_output=True, text=True, env=e,
                          cwd=cwd)


def make_run(E, env='', cluster='hetzner', keep=False):
    p = E.project(extra='spares = 0', env=env)
    E.n += 1
    rc, out, cl = E.submit(p, *(['--keep-scratch'] if keep else []), cluster=cluster, now=f'20261004T01{E.n:04d}Z')
    assert rc == 0, out
    return pathlib.Path(re.search(r'snapshot:\s+(\S+)', out).group(1))


def env_cases(E):
    rd = make_run(E)
    res = run_job(E, rd / 'chunk.sl', rd, rd)
    appt = json.loads(E.appt.read_text().splitlines()[-1]) if E.appt.exists() else []
    envs = [appt[i + 1] for i, x in enumerate(appt) if x == '--env']
    check('env: NO [env] -> the job runs, no empty --env [EXTRA_ENV=( \'\' )]', res.returncode == 0 and '' not in envs,
          res.stdout[-300:] + res.stderr[-300:])
    check('env: run_uuid, n_cores from SLURM_NTASKS and the rclone settings reach the container',
          'run_uuid=t_run' in envs and 'n_cores=48' in envs and 'RCLONE_TIMEOUT=60s' in envs, envs)
    p = E.project()
    (p / 'launcher.toml').write_text('[env]  # extra container env\nWVT_TRMASK_2D = "1"\nNOTE = "a b=c \'q\'"  # comment\n'
                                     'n_cores_metgrid = 4\n\n[launcher]\nimage_name = "wrf-auto-runs-test"\n'
                                     'image_version = "9.9"\nspares = 0\n')
    rc, out, _ = E.submit(p, now='20261004T020000Z')
    rd = pathlib.Path(re.search(r'snapshot:\s+(\S+)', out).group(1))
    res = run_job(E, rd / 'chunk.sl', rd, rd)
    appt = json.loads(E.appt.read_text().splitlines()[-1]) if E.appt.exists() else []
    envs = [appt[i + 1] for i, x in enumerate(appt) if x == '--env']
    check('env: 3 entries all reach the container (incl. spaces/=/quotes and n_cores_metgrid); [launcher] keys after [env] do not',
          res.returncode == 0 and {'WVT_TRMASK_2D=1', "NOTE=a b=c 'q'", 'n_cores_metgrid=4'} <= set(envs)
          and not any(x.startswith(('image_', 'spares')) for x in envs), envs)
    for env, label in (('[env]\nrun_uuid = "x"', 'reserved run_uuid'), ('[env]\nend_date = "2030-01-01"', 'reserved end_date'),
                       ('[env]\nRCLONE_TIMEOUT = "5m"', 'reserved RCLONE_TIMEOUT'), ('[env]\nX = "a,b"', 'a comma'),
                       ('[env]\nX = """\nmulti\n"""', 'a multi-line value'), ('[env]\n9X = "1"', 'a bad name'),
                       ('[env]\nX = [1, 2]', 'an array')):
        p = E.project(env=env)
        rc, out, cl = E.submit(p)
        check(f'env: {label} -> refused, zero sbatch', rc == 1 and not cl, out[-150:])


def validation_cases(E):
    cases = [
        (dict(enable='false'), {}, 'enable must be true', '[restart] enable = false'),
        (dict(stop='false'), {}, 'stop_after_upload', 'stop_after_upload = false'),
        (dict(interval=0), {}, 'interval_days', 'interval_days = 0'),
        (dict(interval='"7d"'), {}, 'interval_days', 'interval_days not an integer'),
        (dict(path=''), {}, 'remote.output', 'no [remote.output] path'),
        (dict(ak='<<<SET-BEFORE-RUN>>>'), {}, 'SET-BEFORE-RUN', 'placeholder credential'),
        (dict(top='preprocess_only = true'), {}, 'preprocess_only', 'preprocess_only = true'),
        (dict(start=''), {}, 'start_date', 'empty start_date'),
        (dict(end=''), {}, 'end_date is empty', 'empty end_date'),
        (dict(end=None), {}, 'needs end_date or duration_hours', 'neither end_date nor duration_hours'),
        (dict(end=None, dur='"x"'), {}, 'duration_hours', 'duration_hours not an integer'),
        (dict(end='2021-06-01 00:00:00'), {}, 'end after it starts', 'end before start'),
        (dict(end='not a date'), {}, 'is not YYYY-MM-DD', 'unparseable end_date'),
        (dict(begin='"24"x'), {}, 'begin_hours', 'begin_hours not an integer'),
        (dict(extra='spares = -1'), {}, 'spares', 'spares = -1'),
        (dict(extra='spare = 2'), {}, "unknown key 'spare'", 'typo key in [launcher]'),
        (dict(extra='[launcher.env]\nX = "1"'), {}, 'unknown table', 'unknown table'),
        (dict(), {'cluster': 'nosuch'}, 'no [nosuch] table', 'unknown cluster'),
        (dict(), {'cluster': 'nontasks'}, 'no --ntasks', 'no --ntasks anywhere'),
        (dict(), {'cluster': 'dollar'}, 'literal', 'a $VARIABLE in a site path'),
        (dict(), {'cluster': 'noscratch'}, 'scratch_base', 'no scratch_base'),
        (dict(), {'cluster': 'placeholder'}, 'placeholder', 'a <<<placeholder>>> left in a site value'),
    ]
    for pkw, skw, msg, label in cases:
        p = E.project(**pkw)
        rc, out, cl = E.submit(p, **skw)
        check(f'validate: {label} -> refused with its reason, zero sbatch', rc == 1 and not cl and msg in out, out[-200:])
    p = E.project()
    (p / 'launcher.toml').write_text('[launcher]\nimage_version = "1"\n')
    rc, out, cl = E.submit(p)
    check('validate: no image_name -> refused', rc == 1 and not cl and 'image_name' in out, out[-150:])
    p = E.project(uuid='ok')
    bad = E.tmp / 'a,b'
    shutil.copytree(p, bad)
    rc, out, cl = E.submit(bad)
    check("validate: a ',' in the project path -> refused [breaks --bind]", rc == 1 and not cl, out[-150:])


def rollback_cases(E):
    p = E.project(extra='spares = 0')
    rc, out, cl = E.submit(p, extra_env={'FAKE_FAIL_AT': '3'})
    cancelled = E.cancel.read_text().splitlines() if E.cancel.exists() else []
    me = os.environ.get('USER') or __import__('pwd').getpwuid(os.getuid()).pw_name
    check('rollback: sbatch fails at job 3 -> exit 1; the 2 ids cancelled NEWEST FIRST, then the chain by name [order / leftovers]',
          rc == 1 and len(cl) == 2 and cancelled == [str(cl[1]['id']), str(cl[0]['id']), f'-u {me} -n wrf-t_run']
          and 'Cancelled.' in out, (cancelled, out[-200:]))
    p = E.project(extra='spares = 0')
    rc, out, cl = E.submit(p, extra_env={'FAKE_FAIL_AT': '1'})
    cancelled = E.cancel.read_text().splitlines() if E.cancel.exists() else []
    check('rollback: sbatch fails at job 1 -> exit 1, no ids, only the by-name cancel, no crash under set -u',
          rc == 1 and not cl and cancelled == [f'-u {me} -n wrf-t_run'] and 'unbound' not in out, (cancelled, out[-200:]))


def runtime_cases(E):
    rd = make_run(E)
    res = run_job(E, rd / 'chunk.sl', rd, rd)
    appt = json.loads(E.appt.read_text().splitlines()[-1]) if E.appt.exists() else []
    bind = appt[appt.index('--bind') + 1] if '--bind' in appt else ''
    check('runtime: --cleanenv --contain --writable-tmpfs and the pipeline command',
          appt[:4] == ['exec', '--cleanenv', '--contain', '--writable-tmpfs'] and appt[-1] == 'cd /app && uv run python -u main.py',
          appt)
    scr = E.scratch / '4242'
    check('runtime: binds params, WPS_GEOG ro, scratch:/data, /dev/shm and scratch/apptainer_tmp:/tmp [ERA5 truncation]',
          bind.split(',') == [f'{rd}/parameters.toml:/app/parameters.toml', f'{E.shared}/WPS_GEOG:/WPS_GEOG:ro',
                              f'{scr}:/data', '/dev/shm:/dev/shm', f'{scr}/apptainer_tmp:/tmp'], bind)
    check('runtime: rc 0 -> exit 0 and scratch removed', res.returncode == 0 and not scr.exists(), res.stdout[-200:])
    res = run_job(E, rd / 'chunk.sl', rd, rd, rc=3)
    check('runtime: rc 3 -> job exits 3 and scratch KEPT', res.returncode == 3 and scr.exists(), res.stdout[-200:])
    shutil.rmtree(scr, ignore_errors=True)
    res = run_job(E, rd / 'chunk.sl', rd, rd, out='-- Upload FAILED in 3 mins (rclone exit 1)')
    check('runtime: upload failure (the pipeline\'s real message) with rc 0 -> scratch KEPT and the job exits 75 [reads as success]',
          res.returncode == 75 and scr.exists() and 'FAILED' in res.stdout, (res.returncode, res.stdout[-200:]))
    shutil.rmtree(scr, ignore_errors=True)
    res = run_job(E, rd / 'chunk.sl', rd, rd, extra_env={'KEEP_SCRATCH': '1'})
    check('runtime: KEEP_SCRATCH=1 leaking from the login shell is ignored; job.env decides (removed) [stale export]', not scr.exists())
    rdk = make_run(E, keep=True)
    res = run_job(E, rdk / 'chunk.sl', rdk, rdk)
    check('runtime: submit --keep-scratch -> scratch KEPT on success', res.returncode == 0 and scr.exists(), res.stdout[-200:])
    shutil.rmtree(scr, ignore_errors=True)
    rdn = make_run(E, cluster='nesi')
    ml = E.tmp / 'module.log'
    if ml.exists():
        ml.unlink()
    res = run_job(E, rdn / 'chunk.sl', rdn, rdn, path_bins=[E.modbin])
    check('runtime: no apptainer on PATH + module_load -> module purge; module load Apptainer',
          ml.exists() and ml.read_text().split('\n')[:2] == ['purge', 'load Apptainer'], ml.read_text() if ml.exists() else res.stderr)
    if ml.exists():
        ml.unlink()
    res = run_job(E, rd / 'chunk.sl', rd, rd, path_bins=[E.modbin])
    check('runtime: no apptainer and no module_load -> exit 1 with a clear message, module not called',
          res.returncode == 1 and 'no module_load' in res.stdout and not ml.exists(), res.stdout[-200:])
    res = run_job(E, rd / 'chunk.sl', rd, rd)
    check('runtime: apptainer on PATH -> module never called', not ml.exists())
    res = run_job(E, rd / 'chunk.sl', rd, rd, arg=False)
    check('runtime: no $1 -> exit 1 "submit with launcher/submit", apptainer never run',
          res.returncode == 1 and 'launcher/submit' in res.stdout and not E.appt.exists(), res.stdout[-200:])

def signal_cases(E):
    """Ctrl-C / TERM / a dropped ssh (HUP) part-way through: every job the controller accepted is cancelled, including
    one accepted after the signal arrived but before its id was recorded (round wrf-launcher-code-1)."""
    import signal
    import time
    me = os.environ.get('USER') or __import__('pwd').getpwuid(os.getuid()).pw_name
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        p = E.project(extra='spares = 0')
        e = E.env(f'2026100{sig}T000000Z', {'FAKE_SLEEP': '0.3'})
        pr = subprocess.Popen(['bash', str(SUBMIT), '--cluster', 'hetzner', '--site', str(E.site), str(p)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=e, cwd=E.tmp)
        t0 = time.time()
        while time.time() - t0 < 20 and (not E.log.exists() or len(E.log.read_text().splitlines()) < 3):
            time.sleep(0.05)
        time.sleep(0.15)                                  # land INSIDE the next sbatch call
        pr.send_signal(sig)
        out, _ = pr.communicate(timeout=30)
        cancelled = E.cancel.read_text().splitlines() if E.cancel.exists() else []
        check(f'signal {sig.name} mid-chain -> exit 1 and the chain cancelled BY NAME too [in-flight job orphaned]',
              pr.returncode == 1 and f'-u {me} -n wrf-t_run' in cancelled, (pr.returncode, cancelled, out[-200:]))
    p = E.project(extra='spares = 0')
    rc, out, cl = E.submit(p, extra_env={'FAKE_FAIL_AT': '3', 'FAKE_SCANCEL_FAIL': '1'}, now='20261005T000001Z')
    check('rollback: scancel itself fails -> says so, never "Cancelled." [overclaimed rollback]',
          rc == 1 and 'scancel FAILED' in out and 'Cancelled.' not in out, out[-300:])
    p = E.project(extra='spares = 0')
    rc, out, cl = E.submit(p, extra_env={'FAKE_GARBAGE_AT': '2'}, now='20261005T000002Z')
    cancelled = E.cancel.read_text().splitlines() if E.cancel.exists() else []
    check('rollback: sbatch exits 0 but prints no job id -> rollback, incl. the by-name cancel [garbage id accepted]',
          rc == 1 and 'not a job id' in out and f'-u {me} -n wrf-t_run' in cancelled, (cancelled, out[-200:]))


def review_cases(E):
    """Cases added for the surviving mutants of round wrf-launcher-code-1, and for its fixes."""
    # -- paths
    p = E.project(name="p q'r", extra='spares = 0')
    E.submit(p, now='20261006T000000Z', extra_env={'FAKE_SPOOL': str(E.tmp / 'spool_q')})
    rd = next((p / 'runs').iterdir())
    res = run_job(E, sorted((E.tmp / 'spool_q').glob('*.sl'))[0], rd, rd)
    appt = json.loads(E.appt.read_text().splitlines()[-1]) if E.appt.exists() else []
    bind = appt[appt.index('--bind') + 1] if '--bind' in appt else ''
    check("paths: a project dir with a space and a quote round-trips through job.env (%q) to the bind [quoting]",
          res.returncode == 0 and bind.startswith(f'{rd}/parameters.toml:/app/'), (res.stdout[-200:], bind))
    p = E.project(uuid='ok')
    shutil.copytree(p, E.tmp / 'a:b')
    rc, out, cl = E.submit(E.tmp / 'a:b')
    check("paths: a ':' in the project path -> refused [breaks --bind]", rc == 1 and not cl and "',' or ':'" in out, out[-150:])
    # -- window rules
    p = E.project(end='2021-12-01 00:00:00', dur=24, begin=0, extra='spares = 0')
    rc, out, _ = E.submit(p, '--dry-run')
    check('count: end_date AND duration_hours -> end_date wins, as in the pipeline [duration_hours preferred]',
          njobs(out)[1] == pipeline_chunks(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 12, 1), 0, 28), out[-200:])
    p = E.project()
    (p / 'parameters.toml').write_text((p / 'parameters.toml').read_text().replace('begin_hours = 672', ''))
    rc, out, cl = E.submit(p)
    check('validate: begin_hours missing -> refused (the pipeline requires it)', rc == 1 and not cl and 'begin_hours' in out, out[-150:])
    p = E.project(end='2021-07-01 00:00:00')
    rc, out, cl = E.submit(p)
    check('validate: end == start -> refused', rc == 1 and not cl and 'end after it starts' in out, out[-150:])
    for bad in ('now', '2021-7-1', '1 week ago'):
        p = E.project(start=bad)
        rc, out, cl = E.submit(p)
        check(f'validate: start_date "{bad}" (GNU date takes it, the pipeline does not) -> refused', rc == 1 and not cl, out[-150:])
    p = E.project(start='2000-01-01 00:00:00', end='2045-01-01 00:00:00', begin=0, extra='spares = 0')
    rc, out, cl = E.submit(p)
    rc2, out2, _ = E.submit(p, '--dry-run', '--max-jobs', '600')
    check('max-jobs: a 45-year window (588 jobs) -> refused; --max-jobs 600 lets it through [typo\'d end_date queues hundreds]',
          rc == 1 and not cl and '--max-jobs' in out and rc2 == 0 and njobs(out2)[0] == 588, (out[-150:], out2[-150:]))
    # -- integers and whitespace
    p = E.project(extra='spares = 010')
    _, out, _ = E.submit(p, '--dry-run')
    p8 = E.project(extra='spares = 08')
    _, out8, _ = E.submit(p8, '--dry-run')
    check('ints: spares = 010 -> 10 and 08 -> 8 (base 10, not octal)', njobs(out)[0] == 25 and njobs(out8)[0] == 23,
          (njobs(out), njobs(out8), out8[-150:]))
    p = E.project(enable='true\t', extra='spares = 0')
    rc, out, _ = E.submit(p, '--dry-run', cluster='trailing')
    check('whitespace: trailing tabs/spaces after values (params and site) are trimmed', rc == 0 and f'{E.shared}/wrf-auto-runs' in out,
          out[-200:])
    # -- [env] and credentials
    reserved = ('run_uuid start_date end_date duration_hours domains n_cores n_cores_preprocess preprocess_only cleanup_inputs '
                'upload_end_frame restart_enable restart_interval_days restart_stop_after_upload TZ HYDRA_LAUNCHER HYDRA_IFACE '
                'RCLONE_TIMEOUT RCLONE_CONTIMEOUT RCLONE_LOW_LEVEL_RETRIES RCLONE_RETRIES').split()
    let = [n for n in reserved if E.submit(E.project(env=f'[env]\n{n} = "1"'), now='20261006T000100Z')[0] != 1]
    check('env: EVERY reserved name is refused (the pipeline\'s env switches + what chunk.sl sets)', not let, let)
    p = E.project(sk='<<<SET-BEFORE-RUN>>>')
    rc, out, cl = E.submit(p)
    check('validate: secret_access_key still <<<SET-BEFORE-RUN>>> -> refused', rc == 1 and not cl and 'SET-BEFORE-RUN' in out, out[-150:])
    # -- ntasks forms, uuid, args
    rc, out, _ = E.submit(E.project(), '--dry-run', cluster='shortn')
    check('ntasks: the short form -n 48 counts', rc == 0, out[-150:])
    rc, out, cl = E.submit(E.project(), cluster='percore')
    check('ntasks: --ntasks-per-core alone is NOT --ntasks -> refused', rc == 1 and not cl and 'no --ntasks' in out, out[-150:])
    rc, out, cl = E.submit(E.project(uuid='a/b'))
    check('uuid: a "/" -> refused', rc == 1 and not cl, out[-150:])
    pr = subprocess.run(['bash', str(SUBMIT), str(E.project()), '--site', str(E.site), '--cluster'], capture_output=True, text=True,
                        env=E.env('20261006T000200Z'))
    check('args: --cluster with no value -> a message, not a silent exit', pr.returncode == 1 and 'needs a value' in pr.stderr,
          pr.stderr[-150:])
    # -- dry run touches nothing
    p = E.project()
    before = sorted(str(x) for x in E.tmp.rglob('*'))
    sq = E.tmp / 'sq.log'
    rc, out, cl = E.submit(p, '--dry-run', extra_env={'FAKE_SQ_LOG': str(sq)})
    after = sorted(str(x) for x in E.tmp.rglob('*'))
    check('dry run: no file created anywhere, squeue never called, a --test-only line printed with the checkout chunk.sl',
          rc == 0 and before == after and not sq.exists() and 'sbatch --test-only' in out and f'{SUBMIT.parent}/chunk.sl' in out,
          (set(after) - set(before), out[-200:]))
    # -- squeue header: -h must be passed
    rc, out, cl = E.submit(E.project(), extra_env={'QUEUED_NAME': 'wrf-other'}, now='20261006T000300Z')
    check('queue: real squeue prints a header without -h; with -h an unrelated queue allows the submit', rc == 0 and len(cl) == 18,
          out[-150:])
    # -- umask and stale KEEP_SCRATCH on the submit side
    rc, out, cl = E.submit(E.project(), umask=0o022, extra_env={'KEEP_SCRATCH': '1'}, now='20261006T000400Z')
    rd = pathlib.Path(re.search(r'snapshot:\s+(\S+)', out).group(1))
    modes = {f.name: oct(f.stat().st_mode & 0o077) for f in rd.iterdir()}
    check('umask: sbatch (so every job) runs with the caller\'s umask 022, not the snapshot\'s 077 [jobs\' files go private]',
          rc == 0 and all(c['umask'] == 0o022 for c in cl), [c['umask'] for c in cl[:2]])
    check('snapshot: EVERY snapshot file is group/world-unreadable', all(m == '0o0' for m in modes.values()), modes)
    check('snapshot: a KEEP_SCRATCH=1 in the submitting shell does not reach job.env (only --keep-scratch does)',
          "KEEP_SCRATCH=''" in (rd / 'job.env').read_text(), (rd / 'job.env').read_text())
    # -- header
    hdr = [ln.split(None, 1)[1].split('#')[0].strip() for ln in CHUNK.read_text().splitlines() if ln.startswith('#SBATCH')]
    check('chunk.sl: the #SBATCH header is exactly --nodes=1 + the two log names (every resource option comes from the site)',
          hdr == ['--nodes=1', '--output=log_chunk_%j.log', '--error=log_chunk_%j.err'], hdr)
    check('chunk.sl: shebang is #!/bin/bash -e', CHUNK.read_text().splitlines()[0] == '#!/bin/bash -e')


def runtime_more_cases(E):
    rd = make_run(E)
    scr = E.scratch / '4242'
    sib = E.scratch / '9999' / 'run'
    sib.mkdir(parents=True)
    (sib / 'wrfout_d01_x').write_text('only copy')
    res = run_job(E, rd / 'chunk.sl', rd, rd, out='pipeline-stdout-marker')
    appt = json.loads(E.appt.read_text().splitlines()[-1])
    envs = [appt[i + 1] for i, x in enumerate(appt) if x == '--env']
    want = ['TZ=UTC', 'n_cores=48', 'n_cores_preprocess=48', 'HYDRA_LAUNCHER=fork', 'HYDRA_IFACE=lo', 'run_uuid=t_run',
            'RCLONE_TIMEOUT=60s', 'RCLONE_CONTIMEOUT=30s', 'RCLONE_LOW_LEVEL_RETRIES=3', 'RCLONE_RETRIES=5']
    check('runtime: EVERY standard container env entry is passed', envs == want, envs)
    cache = json.loads((pathlib.Path(str(E.appt) + '.env')).read_text())
    check('runtime: Apptainer cache/tmp exported under shared_base and created',
          cache == {'APPTAINER_CACHEDIR': f'{E.shared}/.apptainer/cache', 'APPTAINER_TMPDIR': f'{E.shared}/.apptainer/tmp'}
          and (E.shared / '.apptainer' / 'cache').is_dir(), cache)
    check('runtime: the pipeline\'s stdout AND stderr reach the job log (2>&1 | tee)',
          'pipeline-stdout-marker' in res.stdout and 'pipeline-stderr-marker' in res.stdout, res.stdout[-300:])
    check('runtime: a sibling job\'s scratch survives a clean job', (sib / 'wrfout_d01_x').exists())
    res = run_job(E, rd / 'chunk.sl', rd, rd, extra_env={'SLURM_JOB_ID': ''})
    check('runtime: empty SLURM_JOB_ID -> exit non-zero BEFORE anything runs; the scratch base survives [rm -rf of all scratch]',
          res.returncode != 0 and not E.appt.exists() and (sib / 'wrfout_d01_x').exists(), res.stderr[-200:])
    res = run_job(E, rd / 'chunk.sl', rd, rd, rc=1)
    check('runtime: rc 1 -> job exits 1, scratch kept', res.returncode == 1 and scr.exists())
    shutil.rmtree(scr, ignore_errors=True)
    rdl = make_run(E)
    res = run_job(E, rdl / 'chunk.sl', rdl, rdl, extra_env={'EXTRA_ENV': 'LEAK=1'})
    appt = json.loads(E.appt.read_text().splitlines()[-1]) if E.appt.exists() else []
    check('runtime: an EXTRA_ENV exported in the login shell never reaches the container', res.returncode == 0 and 'LEAK=1' not in appt,
          appt)
    for what, path in (('SIF', E.shared / 'wrf-auto-runs-test_9.9.sif'), ('WPS_GEOG', E.shared / 'WPS_GEOG'),
                       ('params', rdl / 'parameters.toml')):
        moved = path.with_name(path.name + '.away')
        path.rename(moved)
        res = run_job(E, rdl / 'chunk.sl', rdl, rdl)
        moved.rename(path)
        check(f'runtime: {what} missing -> exit 1 before apptainer runs', res.returncode == 1 and not E.appt.exists(), res.stdout[-150:])
    (E.modbin / 'module').write_text('#!/bin/bash\necho "$*" >> "$FAKE_MODULE_LOG"\n[ "$1" = load ] && exit 1\nexit 0\n')
    rdn = make_run(E, cluster='nesi')
    res = run_job(E, rdn / 'chunk.sl', rdn, rdn, path_bins=[E.modbin])
    check('runtime: a failing `module load` stops the job (bash -e) before apptainer', res.returncode != 0 and not E.appt.exists(),
          res.stdout[-150:])

def hook_cases(E):
    """Optional hooks/pre (fatal) and hooks/post (warning only), snapshotted like everything else."""
    def with_hooks(pre=None, post=None, extra=None):
        p = E.project(extra='spares = 0')
        (p / 'hooks').mkdir()
        rec = 'echo "$0 RUN_UUID=$RUN_UUID SIF=$SIF_PATH PARAMS=$PARAMS_FILE IMAGE=$IMAGE_NAME:$IMAGE_VERSION RC=${CHUNK_RC:-}" >> "$HOOK_LOG"\n'
        if pre is not None:
            (p / 'hooks' / 'pre').write_text(rec + pre)
        if post is not None:
            (p / 'hooks' / 'post').write_text(rec + post)
        for name, body in (extra or {}).items():
            (p / 'hooks' / name).write_text(body)
        return p
    log = E.tmp / 'hook.log'
    def run(p, rc=0, now='20261007T000000Z'):
        if log.exists():
            log.unlink()
        r, out, _ = E.submit(p, now=now)
        rd = pathlib.Path(re.search(r'snapshot:\s+(\S+)', out).group(1))
        return rd, run_job(E, rd / 'chunk.sl', rd, rd, rc=rc, extra_env={'HOOK_LOG': str(log)})
    p = with_hooks(pre='exit 0\n', post='exit 0\n', extra={'helper.txt': 'x'})
    rd, res = run(p)
    lines = log.read_text().splitlines() if log.exists() else []
    check('hooks: pre then post run, with RUN_UUID, SIF, the SNAPSHOT params, the image, and CHUNK_RC=0 for post',
          res.returncode == 0 and len(lines) == 2 and lines[0].startswith(f'{rd}/hooks/pre RUN_UUID=t_run SIF={E.shared}/')
          and f'PARAMS={rd}/parameters.toml IMAGE=wrf-auto-runs-test:9.9 RC=' in lines[0] and lines[1].endswith('RC=0')
          and (rd / 'hooks' / 'helper.txt').exists(), (lines, res.stdout[-200:]))
    (p / 'hooks' / 'pre').write_text('echo EDITED >> "$HOOK_LOG"; exit 1\n')
    res = run_job(E, rd / 'chunk.sl', rd, rd, extra_env={'HOOK_LOG': str(log)})
    check('hooks: editing the project hook after submit does not reach the queued job [hook read live]',
          res.returncode == 0 and 'EDITED' not in log.read_text(), log.read_text())
    rd, res = run(with_hooks(pre='exit 0\n', post='exit 0\n'), rc=3, now='20261007T000001Z')
    check('hooks: post gets the pipeline\'s rc (CHUNK_RC=3) and the job still exits 3', res.returncode == 3
          and log.read_text().splitlines()[-1].endswith('RC=3'), log.read_text())
    rd, res = run(with_hooks(pre='exit 1\n', post='exit 0\n'), now='20261007T000002Z')
    check('hooks: pre fails -> job exits 1, the pipeline and post never run [fatal pre swallowed]',
          res.returncode == 1 and not E.appt.exists() and len(log.read_text().splitlines()) == 1 and 'hooks/pre failed' in res.stdout,
          (res.stdout[-200:], log.read_text()))
    rd, res = run(with_hooks(post='exit 1\n'), now='20261007T000003Z')
    check('hooks: post fails -> WARNING, the job exits with the pipeline\'s status (0) [post made fatal / silent]',
          res.returncode == 0 and 'hooks/post failed' in res.stdout and E.appt.exists(), res.stdout[-200:])
    rd, res = run(with_hooks(pre='exit 0\n'), now='20261007T000004Z')
    check('hooks: only a pre hook -> post simply absent, job fine', res.returncode == 0 and len(log.read_text().splitlines()) == 1)
    p = with_hooks(pre='')
    (p / 'hooks' / 'pre').write_text('')
    rc, out, cl = E.submit(p, now='20261007T000005Z')
    check('hooks: an empty hooks/pre -> refused at submit', rc == 1 and not cl and 'empty' in out, out[-150:])
    p = with_hooks(extra={'notes.txt': 'x'})
    rc, out, cl = E.submit(p, now='20261007T000006Z')
    check('hooks: hooks/ with neither pre nor post -> refused (a misnamed hook would silently never run)',
          rc == 1 and not cl and 'neither pre nor post' in out, out[-150:])

def jobname_cases(E):
    """--job-name: every job carries it, and the queue check and the rollback key on it."""
    me = os.environ.get('USER') or __import__('pwd').getpwuid(os.getuid()).pw_name
    p = E.project(extra='spares = 0')
    rc, out, cl = E.submit(p, '--job-name', 'c1-2021', now='20261008T000000Z')
    check('job name: --job-name c1-2021 -> every job carries it (and the summary says so)',
          rc == 0 and cl and all('--job-name=c1-2021' in c['opts'] for c in cl) and 'job name:  c1-2021' in out, out[-200:])
    rc, out, cl = E.submit(E.project(), '--job-name', 'c1-2021', extra_env={'QUEUED_NAME': 'c1-2021'}, now='20261008T000001Z')
    check('job name: the queue check uses it -- c1-2021 queued -> refused [check keyed on the default name]',
          rc == 1 and not cl and 'c1-2021 is already queued' in out, out[-200:])
    rc, out, cl = E.submit(E.project(), '--job-name', 'c1-2021', extra_env={'QUEUED_NAME': 'wrf-t_run'}, now='20261008T000002Z')
    check('job name: ... and only it -- a queued wrf-<uuid> does not block a chain named c1-2021', rc == 0 and len(cl) == 18, out[-200:])
    rc, out, cl = E.submit(E.project(extra='spares = 0'), '--job-name', 'c1-2021', extra_env={'FAKE_FAIL_AT': '2'}, now='20261008T000003Z')
    cancelled = E.cancel.read_text().splitlines() if E.cancel.exists() else []
    check('job name: the rollback cancels by it [orphans left under the custom name]', rc == 1 and f'-u {me} -n c1-2021' in cancelled,
          cancelled)
    rc, out, _ = E.submit(E.project(), '--job-name', 'c1-2021', '--dry-run', now='20261008T000004Z')
    check('job name: the dry run\'s --test-only line uses it', rc == 0 and '--test-only' in out
          and '--job-name=c1-2021' in [ln for ln in out.splitlines() if '--test-only' in ln][0], out[-200:])
    for bad in ('a b', '-x', 'c1/2021'):
        rc, out, cl = E.submit(E.project(), '--job-name', bad, now='20261008T000005Z')
        check(f'job name: "{bad}" -> refused, zero sbatch', rc == 1 and not cl, out[-150:])


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='launcher_test_'))
    try:
        for fn in (count_cases, chain_cases, argv_cases, queue_cases, uuid_cases, snapshot_cases, env_cases,
                   validation_cases, rollback_cases, runtime_cases, signal_cases, review_cases, runtime_more_cases, hook_cases, jobname_cases):
            sub = tmp / fn.__name__
            sub.mkdir()
            fn(Env(sub))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'---- {len(PASS)} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
