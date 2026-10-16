"""Inspect the exact DMG and fail closed on missing public-release evidence.

No app launch, installation, permission request or notarization submission.
The image is mounted read-only and detached in finally. Reports stay local.
"""
import argparse
import hashlib
import json
import os
import unicodedata
from pathlib import Path
import subprocess
import sys
import tempfile

from build_drag_preview import app_manifest, private_parent, verify_app

GATES = (
    'fresh_install', 'real_app_tasks', 'failure_recovery', 'upgrade_rollback',
    'performance', 'regressions_security', 'limited_beta',
)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def command(argv):
    return subprocess.run(argv, capture_output=True, timeout=180)


def evidence_gates(evidence, image_sha256):
    """Human-reviewed evidence is candidate-specific, never inferred from tests."""
    if not isinstance(evidence, dict) or evidence.get('image_sha256') != image_sha256:
        return {key: 'missing_or_different_candidate' for key in GATES}
    rows = evidence.get('gates', {})
    if not isinstance(rows, dict):
        rows = {}
    result = {}
    for key in GATES:
        row = rows.get(key)
        if not isinstance(row, dict) or row.get('status') != 'passed':
            result[key] = 'missing_or_failed'
            continue
        if not all(isinstance(row.get(k), str) and row[k].strip()
                   for k in ('reviewer', 'summary')):
            result[key] = 'review_details_required'
            continue
        refs = row.get('artifacts')
        if not isinstance(refs, list) or not refs:
            result[key] = 'artifact_required'
            continue
        try:
            valid = all(isinstance(ref, dict) and Path(ref['path']).is_absolute()
                        and digest(Path(ref['path'])) == ref['sha256'] for ref in refs)
        except (OSError, KeyError, TypeError, ValueError):
            valid = False
        result[key] = 'passed' if valid else 'artifact_missing_or_changed'
    return result


def inspect(app, image, evidence=None):
    app = app.resolve(strict=True)
    image = image.resolve(strict=True)
    before = app_manifest(app)
    image_hash = digest(image)
    checks = {}
    verify_app(app)
    checks['source_signature'] = True
    verified = command(['/usr/bin/hdiutil', 'verify', str(image)])
    verified.check_returncode()
    checks['image_checksum'] = True
    # A dedicated mountpoint lets cleanup run even if hdiutil output is malformed.
    # Never recursively delete this directory while an image may still be mounted.
    mount_root = Path(tempfile.mkdtemp(prefix='tacto-release-check-')).resolve(strict=True)
    volume = mount_root/'volume'
    volume.mkdir()
    stage = 'attach'
    failure = None
    details = {}
    def assess(key, argv):
        result = command(argv)
        checks[key] = result.returncode == 0
        details[key] = (result.stdout + result.stderr).decode(errors='replace').strip()
    try:
        attached = command(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                            '-noautoopen', '-mountpoint', str(volume), str(image)])
        attached.check_returncode()
        stage = 'verify_packaged'
        packaged = volume/'Tacto.app'
        verify_app(packaged)
        checks['packaged_signature'] = True
        checks['packaged_app_matches'] = app_manifest(packaged) == before
        checks['applications_link'] = (volume/'Applications').is_symlink() and (
            volume/'Applications').readlink() == Path('/Applications')
        checks['volume_entries'] = not (set(unicodedata.normalize('NFC', p.name) for p in volume.iterdir()) - {
            'Tacto.app', 'Applications', '먼저 읽어주세요.txt', '.background',
            '.DS_Store', '.VolumeIcon.icns', '.fseventsd', '.Trashes'})
        stage = 'assess_distribution'
        assess('packaged_gatekeeper_accepted', ['/usr/sbin/spctl', '--assess', '--type',
                                              'execute', str(packaged)])
        assess('packaged_notarization_ticket', ['/usr/bin/xcrun', 'stapler', 'validate', str(packaged)])
        assess('image_gatekeeper_accepted', ['/usr/sbin/spctl', '--assess', '--type', 'open',
                                           '--context', 'context:primary-signature', str(image)])
        assess('image_notarization_ticket', ['/usr/bin/xcrun', 'stapler', 'validate', str(image)])
    except Exception as error:
        failure = {'stage': stage, 'error_class': type(error).__name__}
    finally:
        checks['image_detached'] = False
        for extra in ([], ['-force']):
            try:
                detached = command(['/usr/bin/hdiutil', 'detach', *extra, str(volume)])
                if detached.returncode == 0:
                    checks['image_detached'] = True
                    break
            except (OSError, subprocess.SubprocessError):
                pass
        if not checks['image_detached'] and not os.path.ismount(volume):
            checks['image_detached'] = True
        if checks['image_detached']:
            # Remove only empty owned directories, never the contents of a volume.
            try:
                volume.rmdir()
                mount_root.rmdir()
            except OSError:
                pass
    checks['source_unchanged'] = app_manifest(app) == before and digest(image) == image_hash
    manual = evidence_gates(evidence, image_hash)
    blockers = [key for key, passed in checks.items() if not passed]
    blockers += [key for key, status in manual.items() if status != 'passed']
    if failure:
        blockers.append('inspection_failed')
    return {'schema': 1, 'status': 'check_failed' if failure else (
                'blocked' if blockers else 'ready_for_public_release'),
            'image_sha256': image_hash, 'app_manifest_sha256': hashlib.sha256(
                json.dumps(before, sort_keys=True).encode()).hexdigest(),
            'automated_checks': checks, 'reviewed_evidence': manual, 'blockers': blockers,
            'inspection_error': failure, 'assessment_details': details,
            'retained_mountpoint': str(volume) if not checks['image_detached'] else None,
            'app_launched': False, 'installed': False, 'permissions_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    private_parent(output)
    # Reserve before any checks; an existing report is never replaced.
    with output.open('x', encoding='utf-8') as stream:
        output.chmod(0o600)
        try:
            if sys.platform != 'darwin':
                raise RuntimeError('macOS_required')
            evidence = json.loads(args.evidence.read_text()) if args.evidence else None
            result = inspect(args.app, args.image, evidence)
        except Exception as error:
            result = {'schema': 1, 'status': 'check_failed', 'error_class': type(error).__name__,
                      'stage': 'preflight', 'public_release_allowed': False}
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(result['status'])
    return 0 if result['status'] == 'ready_for_public_release' else 1


if __name__ == '__main__':
    raise SystemExit(main())
