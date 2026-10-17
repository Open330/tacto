"""Read-only sampled evidence of foreground, pointer and focus disturbances.

This observer never activates an app, posts input, restores the pointer, or asks
for permissions. Sampling cannot prove absence of changes between observations;
UNCHANGED_SAMPLED is deliberately not an unconditional safety verdict.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
import math
import sys
import threading
import time


@dataclass(frozen=True)
class InputSnapshot:
    frontmost_pid: int | None = None
    pointer: tuple[float, float] | None = None
    focus_identity: str | None = None
    errors: dict = field(default_factory=dict)
    source: str = "injected"
    selected_text_range: tuple[int, int] | None = None


# The focus identity of an app that has nothing focused. A value, distinct from
# None (unknown), so a change from "nothing" to "a field" is still a change the
# audit can see.
NO_FOCUS = "no-focus"


class MacInputReader:
    """Lazy reader with an explicitly selected, labeled foreground source.

    systemwide (default) cross-checks AXFocusedApplication against a fresh
    HIServices foreground-process sample rather than NSWorkspace's cache.
    application_frontmost independently requires direct AXFrontmost=True on the
    observed foreground app before and after focus reading, plus stable native PID.
    Neither source silently substitutes for the other on error. Opaque focus IDs
    use CFEqual rather than Python wrapper identity. No permission prompts.
    require_selection=True additionally reads AXSelectedTextRange as numeric
    CFRange, bracketed by stable focused-element checks; it never reads text.
    """
    def __init__(self, *, strategy='systemwide', require_selection=False):
        if strategy not in ('systemwide', 'application_frontmost'):
            raise ValueError('unknown_input_reader_strategy')
        if type(require_selection) is not bool:
            raise ValueError('require_selection must be boolean')
        self.require_selection = require_selection
        self.strategy = strategy
        self._frameworks = None
        self._focuses = []

    def _load(self):
        if self._frameworks is None:
            if sys.platform != 'darwin':
                raise RuntimeError('unsupported_platform')
            import AppKit
            import Quartz
            import ApplicationServices
            import CoreFoundation
            self._frameworks = (AppKit, Quartz, ApplicationServices, CoreFoundation)
        return self._frameworks

    @staticmethod
    def _foreground_pid():
        from .native_frontmost import frontmost_pid
        return frontmost_pid()

    def _selected_range(self, ax, cf, application, element, errors):
        """Read numeric CFRange only, bracketed by the same focused identity."""
        selection = None
        try:
            error, before = ax.AXUIElementCopyAttributeValue(application, 'AXFocusedUIElement', None)
            if error != 0 or before is None or not cf.CFEqual(before, element):
                errors['selected_text_range'] = 'focus_changed_before_selection:AXError=' + str(error)
                return None
            error, value = ax.AXUIElementCopyAttributeValue(element, 'AXSelectedTextRange', None)
            if error != 0 or value is None:
                errors['selected_text_range'] = 'selection_unavailable:AXError=' + str(error)
            elif (cf.CFGetTypeID(value) != ax.AXValueGetTypeID()
                  or ax.AXValueGetType(value) != ax.kAXValueCFRangeType):
                errors['selected_text_range'] = 'selection_not_ax_cfrange'
            else:
                ok, native_range = ax.AXValueGetValue(value, ax.kAXValueCFRangeType, None)
                if ok is not True:
                    errors['selected_text_range'] = 'selection_range_decode_failed'
                else:
                    if hasattr(native_range, 'location') and hasattr(native_range, 'length'):
                        pair = (native_range.location, native_range.length)
                    elif isinstance(native_range, (tuple, list)) and len(native_range) == 2:
                        pair = tuple(native_range)
                    else:
                        pair = None
                    if pair is None or any(type(n) is not int or n < 0 for n in pair):
                        errors['selected_text_range'] = 'invalid_selection_range'
                    else:
                        selection = pair
            error, after = ax.AXUIElementCopyAttributeValue(application, 'AXFocusedUIElement', None)
            if error != 0 or after is None or not cf.CFEqual(after, element):
                errors['selected_text_range'] = 'focus_changed_after_selection:AXError=' + str(error)
                return None
        except Exception as exc:
            errors['selected_text_range'] = 'selection_read_failed:' + type(exc).__name__
            return None
        return selection

    def _application_frontmost(self, appkit, ax, cf, pid, pointer, errors):
        """Explicit alternate source, never an automatic system-wide fallback."""
        focus = selection = None
        try:
            if not ax.AXIsProcessTrusted():
                errors['frontmost_pid'] = 'accessibility_permission_missing_for_crosscheck'
                errors['focus_identity'] = 'accessibility_permission_missing'
                return InputSnapshot(None, pointer, None, errors, self.strategy)
            if type(pid) is not int or pid <= 0:
                errors['frontmost_pid'] = 'native_frontmost_unavailable'
                return InputSnapshot(None, pointer, None, errors, self.strategy)
            application = ax.AXUIElementCreateApplication(pid)
            error, frontmost = ax.AXUIElementCopyAttributeValue(application, 'AXFrontmost', None)
            if error != 0 or frontmost is not True:
                errors['frontmost_pid'] = ('application_not_frontmost:AXError=' + str(error)
                                          + ';frontmost=' + str(frontmost)[:40])
                return InputSnapshot(None, pointer, None, errors, self.strategy)
            error, element = ax.AXUIElementCopyAttributeValue(application, 'AXFocusedUIElement', None)
            # Same three-way contract as the systemwide path below: nothing
            # focused is a reading, not a failure.
            if error in (-25205, -25212):
                element = None
                # A reading, and it has to be one: None is what `missing` means,
                # and "there is nothing focused" is not "we could not tell".
                focus = NO_FOCUS
            elif error != 0:
                errors['focus_identity'] = 'AXError:' + str(error)
            if element is not None:
                focus = next((token for owner, old, token in self._focuses
                              if owner == pid and cf.CFEqual(old, element)), None)
                if focus is None:
                    if len(self._focuses) >= 256:
                        errors['focus_identity'] = 'focus_identity_budget_exceeded'
                    else:
                        focus = 'focus-' + str(len(self._focuses) + 1)
                        self._focuses.append((pid, element, focus))
                if self.require_selection:
                    selection = self._selected_range(ax, cf, application, element, errors)
            error, frontmost = ax.AXUIElementCopyAttributeValue(application, 'AXFrontmost', None)
            if error != 0 or frontmost is not True:
                errors['frontmost_pid'] = ('application_frontmost_recheck_failed:AXError=' + str(error)
                                          + ';frontmost=' + str(frontmost)[:40])
            pid_after = self._foreground_pid()
            if pid_after != pid:
                errors['frontmost_pid'] = 'native_frontmost_changed_during_read'
        except Exception as exc:
            errors['reader'] = type(exc).__name__
        return InputSnapshot(pid, pointer, focus, errors, self.strategy, selection)

    def __call__(self):
        try:
            appkit, quartz, ax, cf = self._load()
        except Exception as exc:
            return InputSnapshot(errors={'reader': type(exc).__name__}, source=self.strategy)
        errors = {}
        pid = pointer = focus = selection = None
        try:
            pid = self._foreground_pid()
        except Exception as exc:
            errors['frontmost_pid'] = type(exc).__name__
        try:
            # Creates a read-only event snapshot. Never posts/sends this event.
            event = quartz.CGEventCreate(None)
            if event is None:
                raise RuntimeError('pointer_snapshot_unavailable')
            point = quartz.CGEventGetLocation(event)
            pointer = (float(point.x), float(point.y))
        except Exception as exc:
            errors['pointer'] = type(exc).__name__
        if pid is None:
            errors['focus_identity'] = 'fresh_foreground_unavailable'
            return InputSnapshot(None, pointer, None, errors, self.strategy)
        if self.strategy == 'application_frontmost':
            return self._application_frontmost(appkit, ax, cf, pid, pointer, errors)
        try:
            if not ax.AXIsProcessTrusted():
                errors['frontmost_pid'] = 'accessibility_permission_missing_for_crosscheck'
                errors['focus_identity'] = 'accessibility_permission_missing'
            else:
                native_pid = pid
                system = ax.AXUIElementCreateSystemWide()
                error, focused_app = ax.AXUIElementCopyAttributeValue(system, 'AXFocusedApplication', None)
                if error != 0 or focused_app is None:
                    errors['frontmost_pid'] = ('focused_application_unavailable:AXError=' + str(error)
                                                + ';null=' + str(focused_app is None)
                                                + ';native_pid=' + str(native_pid))
                    errors['focus_identity'] = 'foreground_application_unavailable'
                    pid = None
                else:
                    error, focused_pid = ax.AXUIElementGetPid(focused_app, None)
                    if error != 0 or focused_pid <= 0:
                        errors['frontmost_pid'] = ('focused_application_pid_unavailable:AXError=' + str(error)
                                                    + ';pid=' + str(focused_pid))
                        pid = None
                    else:
                        pid = int(focused_pid)
                        if pid != native_pid:
                            errors['frontmost_pid'] = 'native_ax_frontmost_disagree'
                if pid is None:
                    return InputSnapshot(pid, pointer, focus, errors, self.strategy, selection)
                application = ax.AXUIElementCreateApplication(pid)
                error, element = ax.AXUIElementCopyAttributeValue(application, 'AXFocusedUIElement', None)
                # The three-way AX contract this project holds everywhere else:
                # 0 is a value, -25205/-25212 are a LEGITIMATE absence, anything
                # else is a failure. An app in front with nothing focused — a
                # TextEdit whose documents are all closed — answers -25212, and
                # "no focused element" is a reading, not an error. Treated as an
                # error it made the audit refuse to start whenever such an app
                # was frontmost, which three scenarios reported as a broken
                # collector.
                if error in (-25205, -25212):
                    element = None
                    # A reading, and it has to be one: None is what `missing` means,
                    # and "there is nothing focused" is not "we could not tell".
                    focus = NO_FOCUS
                elif error != 0:
                    errors['focus_identity'] = 'AXError:' + str(error)
                elif element is not None:
                    focus = next((token for owner, old, token in self._focuses
                                  if owner == pid and cf.CFEqual(old, element)), None)
                    if focus is None:
                        if len(self._focuses) >= 256:
                            errors['focus_identity'] = 'focus_identity_budget_exceeded'
                        else:
                            focus = 'focus-' + str(len(self._focuses) + 1)
                            self._focuses.append((pid, element, focus))
                    if self.require_selection:
                        selection = self._selected_range(ax, cf, application, element, errors)
                error, focused_after = ax.AXUIElementCopyAttributeValue(system, 'AXFocusedApplication', None)
                if error != 0 or focused_after is None:
                    errors['reader'] = ('focused_application_recheck_failed:AXError=' + str(error)
                                        + ';null=' + str(focused_after is None))
                else:
                    error, pid_after = ax.AXUIElementGetPid(focused_after, None)
                    if error != 0 or pid_after != pid:
                        errors['reader'] = ('ax_frontmost_changed_during_read:AXError=' + str(error)
                                            + ';before_pid=' + str(pid) + ';after_pid=' + str(pid_after))
            pid_after = self._foreground_pid()
            if pid_after != pid:
                errors['reader'] = 'frontmost_changed_during_read'
        except Exception as exc:
            errors['focus_identity'] = type(exc).__name__
        return InputSnapshot(pid, pointer, focus, errors, self.strategy, selection)


class InputAudit:
    """Context-managed periodic collector with injectable reader and clock.

    Example: with InputAudit() as audit: operation(); audit.report() afterwards.
    For deterministic tests use background=False and call sample() explicitly.
    The caller supplies the operation; this class performs observation only.
    AX reads can block: interval is a requested cadence, not a hard deadline.
    Baseline/final reads are synchronous; join_timeout bounds only waiting for
    an already running background reader, whose late result is discarded.
    require_selection=True includes caret/selection range in required evidence;
    unsupported or missing ranges are inconclusive, never assumed unchanged.
    """
    FIELDS = ('frontmost_pid', 'pointer', 'focus_identity')

    def __init__(self, reader=None, *, interval=0.01, clock=time.monotonic,
                 background=True, max_samples=10000, join_timeout=1, require_selection=False):
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
            raise ValueError('interval must be finite and positive')
        if type(max_samples) is not int or max_samples < 2:
            raise ValueError('max_samples must be at least 2')
        if type(join_timeout) not in (int, float) or not math.isfinite(join_timeout) or join_timeout < 0:
            raise ValueError('join_timeout must be finite and nonnegative')
        if type(require_selection) is not bool:
            raise ValueError('require_selection must be boolean')
        self.require_selection = require_selection
        self.fields = self.FIELDS + (('selected_text_range',) if require_selection else ())
        self.reader = reader if reader is not None else MacInputReader(require_selection=require_selection)
        self.interval, self.clock = interval, clock
        self.background, self.max_samples = background, max_samples
        self.join_timeout = join_timeout
        self._lock = threading.Lock()
        self._sampling = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._samples = []
        self._errors = []
        self._started = self._finished = False

    def sample(self):
        with self._sampling:
            with self._lock:
                if self._finished:
                    return
                if len(self._samples) >= self.max_samples:
                    if not any(e['error'] == 'sample_budget_exceeded' for e in self._errors):
                        self._errors.append({'error': 'sample_budget_exceeded'})
                    self._stop.set()
                    return
            try:
                before = self.clock()
                if type(before) not in (int, float) or not math.isfinite(before):
                    raise ValueError('invalid_clock')
                snapshot = self.reader()
                after = self.clock()
                if type(after) not in (int, float) or not math.isfinite(after) or after < before:
                    raise ValueError('invalid_clock')
                with self._lock:
                    previous = self._samples[-1]['finished_at'] if self._samples else None
                if previous is not None and before < previous:
                    raise ValueError('invalid_clock')
                if type(snapshot) is not InputSnapshot:
                    raise TypeError('reader_must_return_InputSnapshot')
                values = {field: getattr(snapshot, field) for field in self.fields}
                errors = dict(snapshot.errors)
                pid = values['frontmost_pid']
                if pid is not None and (type(pid) is not int or pid <= 0):
                    errors['frontmost_pid'] = 'invalid_pid'
                    values['frontmost_pid'] = None
                point = values['pointer']
                if point is not None:
                    if (not isinstance(point, (tuple, list)) or len(point) != 2 or
                            any(type(v) not in (int, float) or not math.isfinite(v) for v in point)):
                        errors['pointer'] = 'invalid_pointer'
                        values['pointer'] = None
                    else:
                        values['pointer'] = tuple(point)
                focus = values['focus_identity']
                if focus is not None and (not isinstance(focus, str) or not focus):
                    errors['focus_identity'] = 'invalid_focus_identity'
                    values['focus_identity'] = None
                if self.require_selection:
                    selection = values['selected_text_range']
                    if selection is not None:
                        if (not isinstance(selection, (tuple, list)) or len(selection) != 2
                                or any(type(n) is not int or n < 0 for n in selection)):
                            errors['selected_text_range'] = 'invalid_selection_range'
                            values['selected_text_range'] = None
                        else:
                            values['selected_text_range'] = tuple(selection)
                record = dict(started_at=before, finished_at=after, values=values,
                              errors=errors, source=snapshot.source, missing=[key for key,value in values.items() if value is None])
            except Exception as exc:
                timestamp = locals().get('before')
                if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
                    timestamp = None
                error = {'clock': 'invalid_or_backwards_timestamp'} if isinstance(exc, ValueError) and str(exc) == 'invalid_clock' else {'reader': type(exc).__name__}
                record = {'started_at': timestamp, 'finished_at': None,
                          'values': {field: None for field in self.fields},
                          'errors': error, 'source': getattr(self.reader, 'strategy', 'injected'), 'missing': list(self.fields)}
            with self._lock:
                if not self._finished:
                    self._samples.append(record)

    def _poll(self):
        while not self._stop.wait(self.interval):
            self.sample()

    def __enter__(self):
        if self._started:
            raise RuntimeError('audit_already_started')
        self._started = True
        self.sample()
        if self.background:
            self._thread = threading.Thread(target=self._poll, name='tacto-input-audit', daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self.join_timeout)
        if self._thread is not None and self._thread.is_alive():
            with self._lock:
                self._errors.append({'error': 'reader_did_not_stop'})
                self._finished = True
        else:
            self.sample()
            with self._lock:
                self._finished = True
        if exc_type is not None:
            with self._lock:
                self._errors.append({'error': 'observed_operation_raised', 'exception': exc_type.__name__})
        return False

    def report(self):
        with self._lock:
            samples, collector_errors = deepcopy(self._samples), deepcopy(self._errors)
            finished = self._finished
        baseline = samples[0] if samples else None
        changes, errors, missing = [], list(collector_errors), []
        for index, sample in enumerate(samples):
            if sample['errors']:
                errors.append({'sample': index, 'at': sample['finished_at'], 'fields': sample['errors']})
            if sample['missing']:
                missing.append({'sample': index, 'at': sample['finished_at'], 'fields': sample['missing']})
            if index and baseline is not None:
                for field in self.fields:
                    old, new = baseline['values'][field], sample['values'][field]
                    if old is not None and new is not None and old != new:
                        changes.append({'sample': index, 'at': sample['finished_at'],
                                        'field': field, 'baseline': old, 'observed': new})
        sources = sorted({str(sample['source']) for sample in samples})
        if len(sources) > 1:
            errors.append({'error': 'reader_strategy_changed'})
        if not finished:
            errors.append({'error': 'audit_not_finished'})
        if len(samples) < 2:
            errors.append({'error': 'insufficient_samples'})
        status = 'CHANGED' if changes else 'INCONCLUSIVE' if errors or missing else 'UNCHANGED_SAMPLED'
        starts = [sample['started_at'] for sample in samples if sample['started_at'] is not None]
        largest_gap = max((right-left for left,right in zip(starts, starts[1:])), default=None)
        return {'status': status, 'evidence_kind': 'sampled_input_observation',
                'proves_no_intermediate_change': False, 'interval_seconds': self.interval,
                'largest_sample_gap_seconds': largest_gap, 'reader_strategies': sources,
                'required_fields': list(self.fields),
                'baseline': baseline, 'samples': samples, 'changes': changes,
                'errors': errors, 'missing': missing}
