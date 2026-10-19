"""Closed evaluator RPC. Only the signed worker owns the document/helper token.

Anonymous pipes are host control channels, not security against arbitrary code
running under the same user. No handles or document tokens cross this protocol.
"""
import json
import os
import selectors
import signal
import subprocess
import time

WORKER_TASKS = ('textedit_edit_owned', 'calc_to_textedit')
MAX_FRAME = 65536
OPERATIONS = frozenset(('check', 'prepare', 'setup', 'oracle', 'empty_oracle', 'evidence', 'cleanup', 'shutdown'))


def serve(comparison, incoming, outgoing, task='textedit_edit_owned'):
    if task not in WORKER_TASKS:
        raise ValueError('unsupported_worker_task')
    sequence = 0
    while True:
        raw = incoming.readline(MAX_FRAME + 1)
        if not raw:
            break
        sequence += 1
        try:
            if len(raw.encode('utf-8')) > MAX_FRAME:
                raise ValueError()
            request = json.loads(raw)
            if (type(request) is not dict or set(request) != {'id', 'op'}
                    or type(request['id']) is not int or request['id'] != sequence
                    or request['op'] not in OPERATIONS):
                raise ValueError()
            op = request['op']
        except (ValueError, TypeError):
            outgoing.write(json.dumps({'id': sequence, 'ok': False, 'error': 'invalid_request'}) + '\n')
            outgoing.flush()
            return 2  # Framing/ordering failure poisons this channel.
        try:
            if op == 'check':
                from tacto.process_responsibility import observe_responsibility
                driver = comparison._driver()
                value = {'capabilities': sorted(driver.capabilities),
                         'task': task, 'pid': os.getpid(), 'responsibility': observe_responsibility(os.getpid())}
            elif op == 'prepare':
                value = comparison.signed_owned_prepare()
            elif op == 'setup':
                setup = (comparison.signed_calc_then_owned_setup() if task == 'calc_to_textedit'
                         else comparison.signed_owned_setup())
                ask = (comparison.signed_chain_ask() if task == 'calc_to_textedit'
                       else comparison.signed_owned_edit_ask())
                value = {'ask': ask, 'evidence': comparison.signed_fixture_evidence(),
                         'setup_evidence': setup}
            elif op == 'oracle':
                value = list(comparison.signed_chain_oracle('437') if task == 'calc_to_textedit'
                             else comparison.signed_owned_oracle(comparison.TEXT))
            elif op == 'empty_oracle':
                fixture = comparison._SIGNED_OWNED.get('fixture')
                body = fixture.read() if fixture is not None else None
                value = {'observable': body is not None, 'exact_empty': body == ''}
            elif op == 'evidence':
                value = comparison.signed_fixture_evidence()
            elif op == 'cleanup':
                fixture = comparison._SIGNED_OWNED.get('fixture')
                status = comparison.signed_owned_cleanup()
                value = {'status': status,
                         'evidence': comparison.signed_fixture_evidence(fixture)}
            else:
                value = {'fixture_left_as_is': bool(comparison._SIGNED_OWNED)}
            response = {'id': sequence, 'ok': True, 'value': value}
        except Exception as exc:
            response = {'id': sequence, 'ok': False, 'error': type(exc).__name__}
        outgoing.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n')
        outgoing.flush()
        if op == 'shutdown':
            return 0
    return 0  # EOF never authorizes an unrequested GUI cleanup.


class FixtureWorker:
    def __init__(self, launcher, *, timeout=45, task='textedit_edit_owned'):
        if task not in WORKER_TASKS:
            raise ValueError('unsupported_worker_task')
        self.timeout, self.sequence, self.buffer, self.poisoned = timeout, 0, b'', False
        env = os.environ.copy()
        for key in ('TACTO_HELPER_FD', 'TACTO_LAUNCHER', 'TACTO_HELPER_TOKEN'):
            env.pop(key, None)
        self.process = subprocess.Popen(
            [launcher, 'eval', '--product-comparison', '--fixture-worker', '--fixture-worker-task', task],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, close_fds=True, start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def request(self, op):
        if self.poisoned or op not in OPERATIONS:
            raise RuntimeError('fixture_worker_channel_unavailable')
        self.sequence += 1
        try:
            self.process.stdin.write(json.dumps({'id': self.sequence, 'op': op}).encode() + b'\n')
            self.process.stdin.flush()
            deadline = time.monotonic() + self.timeout
            while b'\n' not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.selector.select(remaining):
                    raise TimeoutError()
                chunk = os.read(self.process.stdout.fileno(), 4096)
                if not chunk:
                    raise EOFError()
                self.buffer += chunk
                if len(self.buffer) > MAX_FRAME:
                    raise ValueError()
            raw, self.buffer = self.buffer.split(b'\n', 1)
            reply = json.loads(raw)
            if (type(reply) is not dict or type(reply.get('id')) is not int
                    or reply['id'] != self.sequence or type(reply.get('ok')) is not bool
                    or set(reply) != ({'id', 'ok', 'value'} if reply['ok'] else {'id', 'ok', 'error'})):
                raise ValueError()
        except Exception:
            self.poisoned = True
            raise RuntimeError('fixture_worker_transport_unverified') from None
        if not reply['ok']:
            raise RuntimeError('fixture_worker_operation_failed')
        return reply['value']

    def close(self):
        try:
            if self.process.poll() is None and not self.poisoned:
                self.request('shutdown')
        finally:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
            self.selector.close()
            self.process.stdout.close()


def probe_lifecycle(worker, lock_reader, witness_factory):
    """One explicit owned empty-document lifecycle, never retrying UI actions."""
    report = {'scope': 'external_worker_owned_empty_document_lifecycle',
              'status': 'not_run', 'agent_executed': False, 'comparison_eligible': False,
              'setup_requested': False, 'cleanup_status': 'not_requested'}
    if lock_reader() is not False:
        report['reason'] = 'session_unavailable_before_setup'
        return report
    with witness_factory() as witness:
        try:
            report['setup_requested'] = True
            setup = worker.request('setup')
            report['setup_evidence'] = setup['evidence']
            report['empty_document_oracle'] = worker.request('empty_oracle')
        except Exception as error:
            report['status'] = 'failed'
            report['reason'] = type(error).__name__
        # A failed setup can still have created an owned artifact. Obtain only
        # its evidence; never repeat setup or close a document without a token.
        if not worker.poisoned:
            try:
                report['fixture_evidence_before_cleanup'] = worker.request('evidence')
                if lock_reader() is False:
                    cleanup = worker.request('cleanup')
                    report['cleanup_status'] = cleanup['status']
                    report['fixture_evidence_after_cleanup'] = cleanup['evidence']
                else:
                    report['cleanup_status'] = 'session_unavailable_left_as_is'
            except Exception as error:
                report['cleanup_status'] = ('cleanup_effect_unknown' if worker.poisoned
                                            else 'cleanup_unverified')
                report['cleanup_error'] = type(error).__name__
        else:
            report['cleanup_status'] = 'fixture_state_unknown_channel_unavailable'
    report['focus'] = witness.summary()
    initial = report.get('setup_evidence') or {}
    final = report.get('fixture_evidence_after_cleanup') or {}
    report['status'] = ('passed' if not report.get('reason')
                        and initial.get('initial_empty_text_verified') is True
                        and report.get('empty_document_oracle') == {'observable': True, 'exact_empty': True}
                        and report['cleanup_status'] == 'closed'
                        and final.get('closed_observed') is True
                        and final.get('artifact_removed') is True
                        and report['focus'].get('activations') == [] else 'failed')
    return report
