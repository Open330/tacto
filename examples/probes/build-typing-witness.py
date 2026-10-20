"""Build a fresh user-operated witness app; never launch or change focus."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess


def main(output):
    root = Path(__file__).resolve().parents[2]
    source = root / 'tests/fixtures/NativeTypingWitnessFixture.swift'
    output = Path(output)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    bundle = output / 'TACTO Typing Witness.app'
    binaries = bundle / 'Contents/MacOS'
    binaries.mkdir(parents=True)
    copied = output / source.name
    copied.write_bytes(source.read_bytes())
    binary = binaries / 'TypingWitness'
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-warnings-as-errors', str(copied), '-o', str(binary)],
                   check=True, capture_output=True, timeout=45)
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleExecutable': 'TypingWitness', 'CFBundleIdentifier': 'ai.callabo.tacto.user-typing-witness',
        'CFBundleName': 'TACTO Typing Witness', 'CFBundlePackageType': 'APPL',
        'NSHighResolutionCapable': True}))
    subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True, capture_output=True, timeout=15)
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True, capture_output=True, timeout=15)
    report = {'app': str(bundle.resolve()), 'built_not_launched': True, 'source': str(copied.resolve()),
              'source_sha256': hashlib.sha256(copied.read_bytes()).hexdigest(),
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'scope': 'User-operated test text field; counters/selection/hash; no synthetic user typing proof',
              'explicit_foreground_activation_call': False, 'launch_behavior_verified': False,
              'auto_exit_seconds': 3600}
    fd = os.open(output / 'build-report.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='new directory in an existing parent')
    main(parser.parse_args().output)
