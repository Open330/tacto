"""Record one permission revocation/regrant cycle for the release gate.

The person turns Tacto Helper off or on in System Settings; this does the rest:
it checks `doctor`, attempts a fixed task on an owned Finder folder, reads the
result back, and writes a private JSON record. It never changes permissions.

    python3 examples/permission_cycle.py --cycle 1 --phase off   # after turning it off
    python3 examples/permission_cycle.py --cycle 1 --phase on    # after turning it back on

`off` passes when the task is refused before any input (effect not_started)
and nothing in the folder became selected. `on` passes when the same task
then completes and a fresh observation shows exactly the target selected.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

TACTO = Path.home() / '.local/bin/tacto'
ROOT = Path.home() / 'TactoQualification/permission-cycles'


def run(*args, timeout=120, full=False):
    result = subprocess.run([str(TACTO), *args], capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
    return {'argv': list(args), 'exit': result.returncode,
            'stdout': result.stdout if full else result.stdout[-4000:], 'stderr': result.stderr[-2000:]}


def flow(folder, target):
    safety = {'effects': ['write_ephemeral'], 'reversibility': 'reversible',
              'resource': {'object_type': 'owned test file', 'scope': 'entry'}}
    selector = {'role': 'TextField', 'value': target, 'ancestor': folder.name}
    return {'schema': 2, 'name': 'permission-cycle', 'target': 'com.apple.finder', 'steps': [
        {'action': 'open_folder', 'params': {'path': str(folder)},
         'postcondition': {'field': {'role': 'Window', 'title': folder.name}, 'attribute': 'Title',
                           'equals': folder.name}, 'safety': safety},
        {'action': 'click', 'selector': selector,
         'postcondition': {'field': selector, 'contains': '[selected]'}, 'safety': safety}]}


def selected_names(observation, folder, names):
    """Selected files in this phase's own window only; other windows may hold
    earlier phases' selections."""
    lines, inside = [], False
    for line in observation.splitlines():
        if ' Window ' in line and not line.startswith(' '):
            inside = f'Title: "{folder}"' in line
        elif inside:
            lines.append(line)
    return sorted(name for name in names for line in lines if f'Value: {name} [selected]' in line)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cycle', type=int, choices=(1, 2, 3), required=True)
    parser.add_argument('--phase', choices=('off', 'on'), required=True)
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True); ROOT.chmod(0o700)
    # A new folder each phase: an earlier selection cannot count as this one's.
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    folder = ROOT / f'cycle{args.cycle}-{args.phase}-{stamp}'
    folder.mkdir(mode=0o700)
    names = ['alpha.txt', 'target.txt', 'zulu.txt']
    for name in names:
        (folder / name).write_text('permission cycle fixture\n')
    record = {'cycle': args.cycle, 'phase': args.phase, 'started': datetime.datetime.now().isoformat(timespec='seconds'),
              'folder': str(folder), 'launcher': str(TACTO.resolve()),
              'launcher_sha256': hashlib.sha256(TACTO.resolve().read_bytes()).hexdigest()}
    record['doctor'] = run('doctor', '--check')
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as stream:
        json.dump(flow(folder, 'target.txt'), stream)
    try:
        record['task'] = run('run', stream.name, '--helper', '--yes', '--no-indicator')
    finally:
        os.unlink(stream.name)
    observed = run('observe', 'com.apple.finder', '--helper', '--all-windows', full=True)
    record['observe_exit'] = observed['exit']
    window = observed['stdout']
    record['selected_after'] = selected_names(window, folder.name, names) if observed['exit'] == 0 else None
    task_out = record['task']['stdout'] + record['task']['stderr']
    if args.phase == 'off':
        refused = record['task']['exit'] != 0 and 'not_started' in task_out
        record['passed'] = bool(refused and not record['selected_after'])
        record['criterion'] = 'refused before input (not_started); nothing selected where observable'
    else:
        record['passed'] = record['task']['exit'] == 0 and record['selected_after'] == ['target.txt']
        record['criterion'] = 'task completed and fresh observation shows only target.txt selected'
    out = ROOT / f'cycle{args.cycle}-{args.phase}-{stamp}.json'
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1)); out.chmod(0o600)
    print(json.dumps({'cycle': args.cycle, 'phase': args.phase, 'passed': record['passed'],
                      'selected_after': record['selected_after'], 'record': str(out)}, ensure_ascii=False))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
