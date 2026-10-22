"""Safety behaviour of the optional helper modes, measured with the signed command.

Read-only commands only (apps, observe, capture into a private temp file). For
each mode: two commands at once (one driving connection at a time), idle exit,
recovery after the reused helper is killed, fallback when the mode is
unavailable, and whether the accessibility and screen-capture grants still
answer. Esc is not sent: it is the person's key; prototype tests cover it.
"""
import argparse
import concurrent.futures
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path


def run(tacto, argv, mode, extra=None):
    env = dict(os.environ, TACTO_HELPER_MODE=mode, **(extra or {}))
    start = time.monotonic()
    done = subprocess.run([tacto, *argv], env=env, capture_output=True, text=True, timeout=120)
    report = {}
    for line in done.stderr.splitlines():
        if line.startswith('{') and 'tacto_helper_mode' in line:
            try:report = json.loads(line)
            except ValueError:pass
    error = None
    for stream in (done.stdout, done.stderr):
        for line in stream.splitlines():
            if line.startswith('{') and '"error"' in line:
                try:error = json.loads(line).get('error') or error
                except ValueError:pass
    return {'exit': done.returncode, 'ms': round((time.monotonic()-start)*1000), 'effective': report.get('effective'),
            'reason': report.get('reason'), 'helper_pid': report.get('helper_pid'), 'error': error}


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except (ProcessLookupError, TypeError):
        return False


def concurrency(tacto, mode, app, rounds=5):
    out = []
    for _ in range(rounds):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            out.append(list(pool.map(lambda _: run(tacto, ['observe', app, '--helper', '--json'], mode), range(2))))
    return out


def idle_exit(tacto, mode, seconds):
    first = run(tacto, ['apps', '--helper'], mode, {'TACTO_HELPER_IDLE_SECONDS': str(seconds)})
    pid, start = first['helper_pid'], time.monotonic()
    while pid and alive(pid) and time.monotonic() - start < seconds + 60:
        time.sleep(0.5)
    return {'first': first, 'exited_after_s': round(time.monotonic() - start, 1) if pid and not alive(pid) else None}


def kill_recovery(tacto, mode):
    first = run(tacto, ['apps', '--helper'], mode)
    pid = first['helper_pid']
    if pid:os.kill(pid, signal.SIGKILL)
    time.sleep(0.5)
    after = run(tacto, ['apps', '--helper'], mode)
    return {'killed_pid': pid, 'after': after, 'new_helper': after['helper_pid'] not in (None, pid)}


def grants(tacto, mode, app):
    observed = run(tacto, ['observe', app, '--helper', '--json'], mode)
    with tempfile.TemporaryDirectory() as folder:
        shot = Path(folder)/'w.png'
        captured = run(tacto, ['capture', app, '--helper', '--output', str(shot)], mode)
        captured['png_bytes'] = shot.stat().st_size if shot.exists() else 0
    return {'accessibility_observe': observed, 'screen_capture': captured}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tacto', required=True)
    parser.add_argument('--mode', required=True, choices=['warm', 'resident', 'per_command'])
    parser.add_argument('--app', default='com.apple.Safari')
    parser.add_argument('--idle-seconds', type=int, default=5)
    parser.add_argument('--skip-idle', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from tacto.session import lock_state
    if lock_state().get('locked') is not False:
        raise SystemExit('screen_locked: nothing measured')
    report = {'mode': args.mode, 'concurrency': concurrency(args.tacto, args.mode, args.app),
              'kill_recovery': kill_recovery(args.tacto, args.mode), 'grants': grants(args.tacto, args.mode, args.app)}
    if not args.skip_idle:
        report['idle_exit'] = idle_exit(args.tacto, args.mode, args.idle_seconds)
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report)[:3000])


if __name__ == '__main__':
    main()
