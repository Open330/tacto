"""Package the already signed dev helper in a temporary SMAppService host.

Build only. Registration is explicit and must be undone after measurements.
Never installs over /Applications/Tacto.app or the normal command/skills.
"""
import argparse, os, plistlib, shutil, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = 'ai.callabo.tacto.prototype.agent'
SERVICE = 'ai.callabo.tacto.prototype.resident'


def build(output, identity):
    app = Path(output).absolute() / 'TactoModes.app'
    if app.exists():
        raise SystemExit('Output exists; unregister the previous host before replacing it')
    helper = ROOT / 'helper/Tacto Helper.app'
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(helper)], check=True)
    resources = app / 'Contents/Resources'; resources.mkdir(parents=True)
    shutil.copytree(helper, resources / 'Tacto Helper.app', symlinks=True)
    macos = app / 'Contents/MacOS'; macos.mkdir()
    agents = app / 'Contents/Library/LaunchAgents'; agents.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='tacto-mode-host-') as folder:
        source = Path(folder) / 'main.swift'
        shutil.copy2(ROOT / 'spike/helper-modes/Host.swift', source)
        subprocess.run(['xcrun', 'swiftc', '-O', str(source), str(ROOT / 'HelperModeProtocol.swift'),
                        '-o', str(macos / 'TactoModes')], check=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'ai.callabo.tacto.prototype.host', 'CFBundleExecutable': 'TactoModes',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'LSUIElement': True}))
    (agents / (AGENT + '.plist')).write_bytes(plistlib.dumps({
        'Label': AGENT, 'BundleProgram': 'Contents/Resources/Tacto Helper.app/Contents/MacOS/TactoHelper',
        'ProgramArguments': ['TactoHelper', '--resident-agent', '--mode-idle', '120'],
        'MachServices': {SERVICE: True}, 'ProcessType': 'Interactive',
        'AssociatedBundleIdentifiers': ['ai.callabo.tacto.prototype.host']}))
    subprocess.run(['codesign', '--force', '--sign', identity, '--options', 'runtime', '--timestamp=none',
                    '--identifier', 'ai.callabo.tacto.prototype.host', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print(app / 'Contents/MacOS/TactoModes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--identity', default=os.environ.get('TACTO_SIGN_IDENTITY'))
    args = parser.parse_args()
    if not args.identity:
        parser.error('--identity or TACTO_SIGN_IDENTITY is required')
    build(args.output, args.identity)
