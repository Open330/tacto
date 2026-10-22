"""Per-command wall time of the signed tacto command, for each helper mode.

Each mode is an environment for the same commands, so modes compare like for
like: today's per-command helper (default), and opt-in modes as they appear
(TACTO_HELPER_MODE=warm|resident). `list` needs no helper and marks the floor
of starting the command itself. Only reads: `apps` and `observe`. A locked
screen stops the run before any screen read.
"""
import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

COMMANDS = {
    'floor': ['list'],
    'apps': ['apps', '--helper'],
    'observe': ['observe', '{app}', '--helper', '--json'],
}


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def run_once(tacto, argv, env):
    """(ms, exit code, effective mode, helper pid) — the mode the launcher reports on stderr."""
    start = time.monotonic()
    done = subprocess.run([tacto, *argv], env=env, capture_output=True, text=True, timeout=120)
    ms = round((time.monotonic() - start) * 1000, 1)
    effective = pid = None
    for line in done.stderr.splitlines():
        if line.startswith('{') and 'tacto_helper_mode' in line:
            try:
                report = json.loads(line)
            except ValueError:
                continue
            effective, pid = report.get('effective'), report.get('helper_pid')
    return ms, done.returncode, effective, pid


def measure(tacto, mode, app, repeats, env_extra=None, locked=None):
    env = dict(os.environ, **(env_extra or {}))
    rows = {}
    for name, template in COMMANDS.items():
        if locked and locked() and name != 'floor':
            rows[name] = {'skipped': 'screen_locked'}
            continue
        argv = [part.replace('{app}', app) for part in template]
        samples, codes, modes, pids = [], [], [], []
        for _ in range(repeats):
            ms, code, effective, pid = run_once(tacto, argv, env)
            samples.append(ms); codes.append(code); modes.append(effective); pids.append(pid)
        wanted = env.get('TACTO_HELPER_MODE')
        # A run that fell back to the per-command helper says nothing about the mode asked for.
        kept = [ms for ms, code, effective in zip(samples, codes, modes)
                if code == 0 and (name == 'floor' or wanted in (None, 'per_command') or effective == wanted)]
        rows[name] = {'first_ms': samples[0], 'median_ms': statistics.median(kept[1:] or kept) if kept else None,
                      'p95_ms': percentile(kept[1:] or kept, .95) if kept else None, 'samples': samples,
                      'effective_modes': modes, 'helper_pids': sorted({p for p in pids if p}),
                      'kept': len(kept), 'failures': sum(code != 0 for code in codes)}
    return {'mode': mode, 'env': env_extra or {}, 'commands': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--app', default='com.apple.Safari')
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--modes', nargs='+', default=['per_command'])
    # The signed launcher, not a development checkout's unsigned entry point:
    # only the signed one may talk to the helper.
    parser.add_argument('--tacto', default=str(Path.home()/'.local/bin/tacto'))
    args = parser.parse_args()
    tacto = args.tacto
    from tacto.session import lock_state
    locked = lambda: lock_state().get('locked') is not False
    envs = {'per_command': {'TACTO_HELPER_MODE': 'per_command'}, 'warm': {'TACTO_HELPER_MODE': 'warm'},
            'resident': {'TACTO_HELPER_MODE': 'resident'}}
    results = [measure(tacto, mode, args.app, args.repeats, envs[mode], locked) for mode in args.modes]
    report = {'tacto': tacto, 'app': args.app, 'repeats': args.repeats, 'results': results,
              'note': 'wall time of whole commands; first_ms includes any cold start'}
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    for result in results:
        for name, row in result['commands'].items():
            print(result['mode'], name, {k: row[k] for k in row if k not in ('samples', 'effective_modes')})


if __name__ == '__main__':
    main()
