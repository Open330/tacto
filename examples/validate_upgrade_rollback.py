"""Exercise real installed command/skill links through upgrade and rollback.

The installed and candidate runtimes must both expose the canonical Tacto
command, skill, and `<data>/tacto/flows` store. This probe does not exercise
removed command aliases or older storage namespaces.
Existing grants are read only; this never changes OS permissions.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import time
import uuid

COMMANDS = ('tacto',)
AGENTS = ('.codex', '.claude')


def launcher_of(runtime):
    """Return the runtime's signed Tacto launcher."""
    candidate = Path(runtime)/'launcher/tacto'
    if candidate.is_file():
        return candidate
    raise ValueError(f'{runtime}: no launcher/tacto')


def bin_dir(environ, home):
    return Path(environ['TACTO_BIN_DIR']) if environ.get('TACTO_BIN_DIR') else Path(home)/'.local/bin'


def data_dir(environ, home):
    configured = environ.get('XDG_DATA_HOME', '')
    return Path(configured) if configured and Path(configured).is_absolute() else Path(home)/'.local/share'


def stores(environ, home):
    data = data_dir(environ, home)
    return {'canonical': data/'tacto/flows'}


def has_flows(path):
    return Path(path).is_dir() and any(p.suffix == '.json' for p in Path(path).iterdir())


def probe_store(environ, home):
    """The canonical store; an exclusively owned probe does not replace user flows."""
    return stores(environ, home)['canonical'], None


def links(environ, home):
    b = bin_dir(environ, home)
    return [b/name for name in COMMANDS] + [Path(home)/agent/'skills/tacto' for agent in AGENTS]


def link_state(paths):
    return {str(p): os.readlink(p) if p.is_symlink() else None for p in paths}


def links_target(runtime, environ, home):
    """The command resolves to this runtime's launcher; both skills to its skill."""
    entry = launcher_of(runtime).resolve()
    b = bin_dir(environ, home)
    return (all((b/name).resolve() == entry for name in COMMANDS)
            and all((Path(home)/agent/'skills/tacto').resolve() == (Path(runtime)/'skill').resolve()
                    for agent in AGENTS))


def manifest(environ, home):
    """Hashes of canonical user store and config files."""
    data = data_dir(environ, home)
    roots = {data/'tacto/flows', Path(home)/'.local/share/tacto/flows',
             Path(home)/'.config/tacto'}
    result = {}
    for root in sorted(roots):
        if root.is_dir():
            for p in root.rglob('*'):
                if p.is_file() and not p.is_symlink():
                    result[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def standalone_runtime(path):
    path = Path(path)
    return (path.name == 'runtime' and path.parent.name == 'Resources'
            and path.parents[1].name == 'Contents' and path.parents[2].suffix == '.app')


def main():
    from release_identity import identity
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--candidate', type=Path, required=True, help='signed standalone Tacto.app')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    environ, home = dict(os.environ), Path.home()
    out = args.output.resolve(); out.mkdir(mode=0o700)
    bins = bin_dir(environ, home)
    old = (bins/'tacto').resolve(strict=True).parent.parent
    new = args.candidate.resolve(strict=True)/'Contents/Resources/runtime'
    assert old != new, 'distinct baseline and candidate required'
    for root in (old, new):
        assert standalone_runtime(root), 'standalone bundle layout required'
        launcher_of(root)
    store, refusal = probe_store(environ, home)
    if refusal:
        (out/'report.json').write_text(json.dumps({'passed': False, 'refused': refusal,
            'detail': 'the upgrade scenario does not apply; nothing was written'}, indent=2))
        raise SystemExit(f'refused: {refusal}')
    watched = links(environ, home)
    original = link_state(watched); before = manifest(environ, home); rows = []
    def call(argv, label):
        r = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        (out/(label+'.log')).write_text(r.stdout+r.stderr)
        if r.returncode:
            raise RuntimeError(label+' failed')
        return r.stdout

    call(['/bin/bash', str(old/'skill/install.sh'), '--check'], 'initial-check')

    # One exclusively owned probe artifact in the canonical store.
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    flow = {'schema': 2, 'name': 'release_probe_'+uuid.uuid4().hex[:12], 'target': 'com.apple.finder',
            'steps': [{'action': 'read', 'selector': {'role': 'Window', 'title': 'no-action-owned-probe'}}]}
    p = store/(flow['name']+'.json')
    with p.open('x') as owned:
        owned.write(json.dumps(flow))
    p.chmod(0o600)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()

    completed = False; reference = None; report = None
    try:
        for stage, root in [('old', old), ('upgrade', new), ('rollback', old), ('restore-old', old)]:
            started = time.monotonic()
            call(['/bin/bash', str(root/'skill/install.sh')], stage+'-install')
            call(['/bin/bash', str(root/'skill/install.sh'), '--check'], stage+'-check')
            doctor = call([str(bins/'tacto'), 'doctor', '--check'], stage+'-doctor')
            for command in COMMANDS:
                decoded = json.loads(call([str(bins/command), 'show', flow['name']], f'{stage}-{command}-show'))
                assert decoded['name'] == flow['name'] and decoded['target'] == flow['target']
                if reference is None:
                    reference = decoded
                assert decoded == reference
            assert hashlib.sha256(p.read_bytes()).hexdigest() == digest
            assert {k: v for k, v in manifest(environ, home).items() if k != str(p)} == before
            call(['codesign', '--verify', '--deep', '--strict', str(root.parents[2])], stage+'-signature')
            assert links_target(root, environ, home)
            rows.append({'candidate_identity': identity(launcher_of(root)), 'stage': stage,
                         'launcher': launcher_of(root).name, 'passed': True,
                         'seconds': time.monotonic()-started,
                         'both_grants_ready': doctor.count('이미 켜져 있어요.') == 2,
                         'saved_flow_preserved': True, 'user_state_unchanged': True,
                         'links': link_state(watched)})
        completed = True
    finally:
        restore_error = None
        try:
            call(['/bin/bash', str(old/'skill/install.sh')], 'restore-original-install')
            call(['/bin/bash', str(old/'skill/install.sh'), '--check'], 'restore-original-check')
            assert link_state(watched) == original
        except Exception as error:
            restore_error = type(error).__name__
            completed = False
        if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == digest:
            p.unlink()
        report = {'passed': completed and link_state(watched) == original and manifest(environ, home) == before
                  and all(r['passed'] and r['both_grants_ready'] for r in rows),
                  'rows': rows, 'restoration_error': restore_error,
                  'original_links_restored': link_state(watched) == original,
                  'original_links': original, 'final_links': link_state(watched),
                  'user_state_unchanged': manifest(environ, home) == before,
                  'probe_store': str(store), 'owned_probe_flow_sha256': digest,
                  'scope': 'actual tacto command and both agent skill links across installed and candidate '
                           'launcher/tacto runtimes, canonical-store preservation, existing grants and '
                           'signatures; no OS permission changes or cold-machine claims',
                  'permissions_requested': False}
        (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'passed': report['passed'], 'stages': [r['stage'] for r in rows],
                          'user_state_unchanged': report['user_state_unchanged']}))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
