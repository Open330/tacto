"""Process-isolated read-only input sampling, independent of the caller's GIL.

The child only observes. Successful sampling is not proof that nothing changed
between samples. Spawn requires the usual if __name__ == '__main__' entry guard;
injected readers must be picklable top-level callables.
"""
from __future__ import annotations
from copy import deepcopy
import math
import multiprocessing
import time

from .input_audit import InputAudit, MacInputReader


class InputAuditProcessError(RuntimeError):
    pass


def _collect(sender, stop, reader, strategy, interval, ready_samples, max_samples, require_selection):
    raw = None
    errors = []
    ready = False
    try:
        reader = reader if reader is not None else MacInputReader(strategy=strategy, require_selection=require_selection)
        attempts = 0
        def tracked_reader():
            nonlocal attempts
            try:
                return reader()
            finally:
                attempts += 1
        tracked_reader.strategy = getattr(reader, 'strategy', 'injected')
        audit = InputAudit(tracked_reader, interval=interval, background=False, max_samples=max_samples, require_selection=require_selection)
        with audit:
            while True:
                if not ready and attempts >= ready_samples:
                    samples = audit.report()['samples']
                    if any(sample['errors'] or sample['missing'] for sample in samples):
                        errors.append({'error': 'initial_samples_inconclusive'})
                        break
                    sender.send({'kind': 'ready', 'sample_count': len(samples)})
                    ready = True
                if stop.is_set():
                    break
                if attempts >= max_samples - 1:
                    errors.append({'error': 'sample_budget_exceeded'})
                    break
                if stop.wait(interval):
                    break
                audit.sample()
        raw = audit.report()
    except BaseException as exc:
        errors.append({'error': 'collector_failed', 'exception': type(exc).__name__})
    try:
        sender.send({'kind': 'result', 'raw_report': raw, 'errors': errors})
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        sender.close()


class ProcessInputAudit:
    """Spawn an observer, await valid initial samples, then enter the operation.

    Startup failure raises InputAuditProcessError before the body executes.
    Stop failures are recorded in report() rather than hiding an operation's
    exception. A terminated child means INCONCLUSIVE, never sampled success.
    require_selection=True requires numeric foreground caret/selection evidence
    in the child. Missing/nontext/unsupported focus prevents readiness.
    """
    def __init__(self, reader=None, *, strategy='application_frontmost', interval=0.01,
                 ready_samples=3, startup_timeout=5, stop_timeout=2, max_samples=10000, require_selection=False):
        if strategy not in ('application_frontmost', 'systemwide'):
            raise ValueError('unknown_input_reader_strategy')
        for name, value in (('interval', interval), ('startup_timeout', startup_timeout),
                            ('stop_timeout', stop_timeout)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' must be finite and positive')
        if type(ready_samples) is not int or ready_samples < 2:
            raise ValueError('ready_samples must be at least 2')
        if type(max_samples) is not int or max_samples <= ready_samples + 1:
            raise ValueError('max_samples must exceed ready_samples + 1')
        if type(require_selection) is not bool:
            raise ValueError('require_selection must be boolean')
        self.require_selection = require_selection
        self.reader, self.strategy, self.interval = reader, strategy, interval
        self.ready_samples, self.startup_timeout = ready_samples, startup_timeout
        self.stop_timeout, self.max_samples = stop_timeout, max_samples
        self._process = self._receiver = self._stop = None
        self._raw = None
        self._errors = []
        self._ready = self._finished = self._started = False
        self._got_result = False

    def _message(self, message):
        if not isinstance(message, dict):
            self._errors.append({'error': 'invalid_collector_message'})
            return
        if message.get('kind') == 'ready':
            count = message.get('sample_count')
            if type(count) is int and count >= self.ready_samples:
                self._ready = True
            else:
                self._errors.append({'error': 'insufficient_ready_samples'})
        elif message.get('kind') == 'result':
            self._raw = message.get('raw_report')
            self._errors.extend(message.get('errors') or [])
            self._got_result = True
        else:
            self._errors.append({'error': 'unknown_collector_message'})

    def _receive(self, wait):
        try:
            if self._receiver.poll(wait):
                self._message(self._receiver.recv())
                return True
        except (EOFError, OSError):
            if not self._got_result:
                self._errors.append({'error': 'collector_connection_closed'})
            return False
        return False

    def __enter__(self):
        if self._started:
            raise InputAuditProcessError('audit_already_started')
        self._started = True
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        self._receiver = receiver
        self._stop = context.Event()
        self._process = context.Process(target=_collect,
            args=(sender, self._stop, self.reader, self.strategy, self.interval,
                  self.ready_samples, self.max_samples, self.require_selection), name='tacto-input-audit-process', daemon=True)
        try:
            self._process.start()
        except Exception as exc:
            self._errors.append({'error': 'collector_start_failed', 'exception': type(exc).__name__})
            sender.close()
            receiver.close()
            self._finished = True
            raise InputAuditProcessError('collector_start_failed') from exc
        sender.close()
        deadline = time.monotonic() + self.startup_timeout
        while not self._ready and not self._got_result:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._errors.append({'error': 'collector_startup_timeout'})
                break
            self._receive(min(0.02, remaining))
            if not self._process.is_alive() and not self._ready:
                self._receive(0)
                if not self._got_result:
                    self._errors.append({'error': 'collector_exited_before_ready'})
                break
        if not self._ready or self._got_result:
            self._errors.append({'error': 'collector_not_ready'})
            self._finish()
            # Name the cause, not only the symptom. `collector_not_ready` alone
            # sent three scenarios' failures to look like one broken collector,
            # when every one of them was the same readable state: the frontmost
            # app had nothing focused, and system-wide AXFocusedApplication
            # answers -25204 in exactly that state.
            causes = [e.get('error') for e in self._errors
                      if e.get('error') != 'collector_not_ready']
            detail = self._first_sample_errors()
            raise InputAuditProcessError(
                'collector_not_ready' + (': ' + ', '.join(causes) if causes else '')
                + (' — ' + detail if detail else ''))
        return self

    def _first_sample_errors(self):
        """The first reason a baseline sample was unusable, if one was kept."""
        report = getattr(self, '_raw', None) or {}
        for sample in report.get('samples') or []:
            if sample.get('errors'):
                return '; '.join(f'{k}={v}' for k, v in sample['errors'].items())
        return ''

    def _finish(self):
        if self._finished:
            return
        if self._stop is not None:
            self._stop.set()
        process = self._process
        if process is not None and process.pid is not None:
            deadline = time.monotonic() + self.stop_timeout
            while not self._got_result and time.monotonic() < deadline:
                self._receive(min(0.02, max(0, deadline-time.monotonic())))
                if not process.is_alive():
                    self._receive(0)
                    break
            process.join(max(0, deadline-time.monotonic()))
            if process.is_alive():
                self._errors.append({'error': 'collector_stop_timeout'})
                process.terminate()
                process.join(min(0.5, self.stop_timeout))
                if process.is_alive():
                    process.kill()
                    process.join(min(0.5, self.stop_timeout))
                if process.is_alive():
                    self._errors.append({'error': 'collector_did_not_exit'})
            if process.exitcode not in (None, 0):
                self._errors.append({'error': 'collector_exit_failed', 'exitcode': process.exitcode})
        if not self._got_result:
            self._errors.append({'error': 'collector_result_missing'})
        if self._receiver is not None:
            self._receiver.close()
        self._finished = True

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self._errors.append({'error': 'observed_operation_raised', 'exception': exc_type.__name__})
        self._finish()
        return False

    def report(self):
        errors = deepcopy(self._errors)
        if not self._finished:
            errors.append({'error': 'audit_not_finished'})
        if not self._ready:
            errors.append({'error': 'collector_was_not_ready'})
        raw = deepcopy(self._raw)
        status = raw.get('status') if isinstance(raw, dict) else None
        if errors or status not in ('UNCHANGED_SAMPLED', 'CHANGED', 'INCONCLUSIVE'):
            status = 'INCONCLUSIVE'
        return {'status': status, 'raw_report': raw, 'errors': errors,
                'child_pid': self._process.pid if self._process is not None else None,
                'ready_samples': self.ready_samples,
                'strategy': self.strategy if self.reader is None else getattr(self.reader, 'strategy', 'injected'),
                'requested_strategy': self.strategy, 'require_selection': self.require_selection,
                'evidence_kind': 'process_isolated_sampled_input_observation',
                'proves_no_intermediate_change': False}
