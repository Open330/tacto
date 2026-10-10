"""One model-free signed Finder preparation on a fresh owned empty folder.

Run with the checkout venv and a NEW report path. GUI actions are performed
only by the installed signed launcher; no Python AX/GUI fallback is used.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'examples'))
from product_comparison import source_manifest
from nlc.contract import ContextBinding, Envelope
from nlc.flow import Flow, Postcondition, Selector, Step, pin_params
from nlc.preparation_diagnostic import diagnostic_lines


def preparation_flow(path):
    target, name = 'com.apple.finder', Path(path).name
    window = Selector(role='Window', title=name)
    envelope = Envelope.minimal()
    return Flow('owned_finder_preparation_diagnostic', target, [
        Step('open_folder', params={'path':path},
             safety=envelope.contract_for({'write_ephemeral'}, 'open owned folder'),
             binding=ContextBinding(bundle_id=target, operation='open_folder', params_digest=pin_params({'path':path})),
             postcondition=Postcondition(field=window, equals=name, attribute='Title')),
        Step('prepare_window', window,
             safety=envelope.contract_for({'write_ephemeral'}, 'prepare owned window'),
             binding=ContextBinding(bundle_id=target, role='Window', title=name,
                                    operation='prepare_window', params_digest=pin_params({})),
             postcondition=Postcondition(field=Selector(role='Window', title=name, in_focused_window=True),
                                         equals=name, attribute='Title')),
    ], schema=2)


def main(report):
    fd = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    directory = os.path.realpath(tempfile.mkdtemp(prefix='nlc-finder-ready-'))
    identity = os.stat(directory)
    result = {'kind':'owned-finder-preparation', 'model_calls':0,
              'owned_directory':directory, 'diagnostics':[], 'cleanup':'left_as_is',
              'source_manifest_before':source_manifest()}
    try:
        with tempfile.TemporaryDirectory(prefix='nlc-finder-flow-') as work:
            flow = Path(work) / 'prepare.json'
            flow.write_text(preparation_flow(directory).to_json())
            launcher = Path.home() / '.local/bin/nlc'
            if launcher.resolve() != ROOT / 'launcher/nlc':
                raise RuntimeError('signed launcher checkout mismatch')
            with subprocess.Popen([str(launcher), 'run', str(flow), '--helper', '--yes', '--think', '0'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  start_new_session=True) as process:
                try:
                    stdout, _ = process.communicate(timeout=60)
                except BaseException:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)
                    raise
                result.update(process_exit=process.returncode, diagnostics=diagnostic_lines(stdout))
        # Remove only the same, still-empty directory we made. Finder windows
        # and all other files are left alone. Timeout/exception never cleans up.
        now = os.lstat(directory)
        if (now.st_dev, now.st_ino) == (identity.st_dev, identity.st_ino) and not os.listdir(directory):
            os.rmdir(directory)
            result['cleanup'] = 'owned_empty_directory_removed'
    except Exception as error:
        result['error_class'] = type(error).__name__
    finally:
        result['source_manifest_after'] = source_manifest()
        result['source_changed'] = result['source_manifest_before'] != result['source_manifest_after']
        with os.fdopen(fd, 'w') as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in
                    ('owned_directory', 'source_manifest_before', 'source_manifest_after')}))
    return 0 if result.get('process_exit') == 0 and not result['source_changed'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
