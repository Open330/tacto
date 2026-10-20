"""One model-free signed Finder preparation on a fresh owned empty folder.

Run with the checkout venv and a NEW report path. GUI actions are performed
only by the installed signed launcher; no Python AX/GUI fallback is used.
"""
import ast
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
from tacto.contract import ContextBinding, Envelope
from tacto.flow import Flow, Postcondition, Selector, Step, pin_params
from tacto.preparation_diagnostic import diagnostic_lines


def action_failure(output):
    """Only bounded effect/count metadata from the CLI's final failure record.

    An absent native count stays unknown; never manufacture zero from a missing
    field. Neither this extraction nor a diagnostic marker authenticates IPC.
    """
    if type(output) is not str or len(output) > 1024 * 1024:
        return None
    for line in reversed(output.splitlines()):
        if not line.startswith('failed: ') or len(line) > 8192:
            continue
        try:
            record = ast.literal_eval(line[len('failed: '):])
        except (ValueError, SyntaxError, RecursionError):
            return None
        detail = record.get('detail') if type(record) is dict else None
        if type(detail) is not dict or detail.get('type') != 'error':
            return None
        effect = detail.get('effect')
        if effect not in ('not_started', 'unknown'):
            return None
        count = detail.get('posted_or_attempted_events')
        if count is not None and (type(count) is not int or not 0 <= count <= 16):
            return None
        return {'effect': effect, 'reported_posted_or_attempted_events': count}
    return None


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
    directory = os.path.realpath(tempfile.mkdtemp(prefix='tacto-finder-ready-'))
    identity = os.stat(directory)
    result = {'kind':'owned-finder-preparation', 'model_calls':0,
              'owned_directory':directory, 'diagnostics':[], 'cleanup':'left_as_is',
              'source_manifest_before':source_manifest()}
    try:
        with tempfile.TemporaryDirectory(prefix='tacto-finder-flow-') as work:
            flow = Path(work) / 'prepare.json'
            flow.write_text(preparation_flow(directory).to_json())
            launcher = Path.home() / '.local/bin/tacto'
            if launcher.resolve() != ROOT / 'launcher/tacto':
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
                result.update(process_exit=process.returncode, diagnostics=diagnostic_lines(stdout),
                              action_failure=action_failure(stdout))
        # The window may still display this directory. Deleting it can make
        # Finder navigate to the larger parent, changing later test conditions.
        # This probe has no window creation/close proof, so retain the resource.
        now = os.lstat(directory)
        if (now.st_dev, now.st_ino) == (identity.st_dev, identity.st_ino) and not os.listdir(directory):
            result['cleanup'] = 'owned_directory_retained_without_window_close_proof'
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
