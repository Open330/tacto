import copy
import unittest
from unittest.mock import Mock

from tacto.contract import Envelope
from tacto.flow import Flow, Postcondition, Selector, Step, replay
from tacto.window_absence import reobserve_closed_window
from tests.test_runtime_contract import App


TARGET = 'owned.app'
DIAGNOSTIC = dict(version=1, status='ok', ax_error=0, raw_count=0,
                  role_window_count=0, role_transient_count=0,
                  role_unavailable_count=0, filtered_count=0, badge_count=0)


def unavailable():
    return dict(type='error', error='no_window_content', target=TARGET,
                reason='window_offscreen', lock=dict(locked=False, on_console=True, login_done=True),
                window_listing_diagnostic=copy.deepcopy(DIAGNOSTIC))


def windowless():
    return dict(type='observation', target=TARGET, obs_id='fresh', tree='',
                element_count=0, truncated=False, window_listing_diagnostic=copy.deepcopy(DIAGNOSTIC))


class ClosingApp(App):
    def __init__(self):
        super().__init__(); self.closed = False; self.windowless_reads = 0

    @property
    def tree(self):
        return '0 Window Title: Owned\n  1 Button Identifier: close, Title: Close'

    def observe(self, target, mode='tree_full', require_window=True):
        if not self.closed:
            return super().observe(target, mode, require_window)
        if require_window:
            return unavailable()
        self.windowless_reads += 1
        return windowless()

    def act(self, *args, **kwargs):
        result = super().act(*args, **kwargs); self.closed = True; return result

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        observation = self.observe(target)
        if observation['type'] == 'error':
            return observation
        return observation if predicate(observation['tree']) else dict(type='timeout', last_obs=observation)


class WindowAbsenceTests(unittest.TestCase):
    def test_success_requires_explicit_window_role_and_fresh_real_observation(self):
        for role in ('Window', 'AXWindow'):
            read = Mock(return_value=windowless())
            result = reobserve_closed_window(read, TARGET, Postcondition(absent_role=role), unavailable())
            self.assertEqual('ax_window_list_empty', result['verification_evidence'])
            self.assertEqual('fresh', result['obs_id'])
            read.assert_called_once_with(TARGET, 'tree_full', require_window=False)

    def test_text_absence_positive_checks_and_other_roles_do_not_get_exception(self):
        for post in (Postcondition(absent='Window'), Postcondition(absent_role='Sheet'),
                     Postcondition(absent_role='Window', absent='x'),
                     Postcondition(absent_role='Window', attribute='Value'),
                     Postcondition(absent_role='Window', equals='done'),
                     Postcondition(absent_role='Window', contains='done'),
                     Postcondition(absent_role='Window', field=Selector(role='Window')),
                     Postcondition(absent_role='Window', all_of=[Postcondition(absent='x')])):
            read = Mock(); failed = unavailable()
            self.assertIs(failed, reobserve_closed_window(read, TARGET, post, failed))
            read.assert_not_called()

    def test_lock_hidden_app_missing_target_and_failed_enumeration_remain_errors(self):
        cases = []
        for key, values in [('locked', [True, None]), ('on_console', [False, None]), ('login_done', [False, None])]:
            for value in values:
                failed = unavailable(); failed['lock'][key] = value; cases.append(failed)
        for error in ('target_not_found', 'aborted_by_user', 'window_list_unavailable', 'observation_unavailable'):
            failed = unavailable(); failed['error'] = error; cases.append(failed)
        for reason in ('app_hidden', 'minimized', 'screen_locked'):
            failed = unavailable(); failed['reason'] = reason; cases.append(failed)
        failed = unavailable(); failed['target'] = 'other.app'; cases.append(failed)
        failed = unavailable(); failed.pop('window_listing_diagnostic'); cases.append(failed)
        for key in DIAGNOSTIC:
            failed = unavailable(); failed['window_listing_diagnostic'][key] = 'invalid'; cases.append(failed)
        for failed in cases:
            with self.subTest(failed=failed):
                read = Mock()
                self.assertIs(failed, reobserve_closed_window(read, TARGET, Postcondition(absent_role='Window'), failed))
                read.assert_not_called()

    def test_incomplete_or_reappearing_windows_cannot_verify_closure(self):
        cases = []
        for key, value in [('target', 'other.app'), ('obs_id', ''), ('element_count', 1),
                           ('truncated', True), ('tree', '0 Window Title: Reappeared')]:
            fresh = windowless(); fresh[key] = value; cases.append(fresh)
        fresh = windowless(); fresh.pop('window_listing_diagnostic'); cases.append(fresh)
        for key in ('role_window_count', 'role_unavailable_count', 'role_transient_count', 'filtered_count'):
            fresh = windowless(); fresh['window_listing_diagnostic'].update({'raw_count': 1, key: 1}); cases.append(fresh)
        for fresh in cases:
            failed = unavailable()
            self.assertIs(failed, reobserve_closed_window(Mock(return_value=fresh), TARGET,
                                                         Postcondition(absent_role='Window'), failed))

    def test_a_listed_entry_that_is_not_a_window_does_not_count(self):
        # Finder keeps its desktop in the window list after its last window
        # closes: raw_count 1, every role read, none of them AXWindow (rc.8).
        failed = unavailable(); failed['window_listing_diagnostic']['raw_count'] = 1
        fresh = windowless(); fresh['window_listing_diagnostic']['raw_count'] = 1
        verified = reobserve_closed_window(Mock(return_value=fresh), TARGET, Postcondition(absent_role='Window'), failed)
        self.assertEqual('ax_window_list_empty', verified.get('verification_evidence'))

    def test_fresh_read_failure_is_preserved(self):
        error = dict(type='error', error='aborted_by_user')
        self.assertIs(error, reobserve_closed_window(Mock(return_value=error), TARGET,
                                                   Postcondition(absent_role='Window'), unavailable()))

    def test_replay_closes_once_and_trace_replays_without_repeating_input(self):
        from tacto.trace import TracingDriver, TraceDriver, Trace
        step = Step('click', Selector(identifier='close'),
                    safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'entry'),
                    postcondition=Postcondition(absent_role='Window'))
        flow = Flow('close-owned-window', TARGET, [step], schema=2)
        app = ClosingApp(); trace = Trace(flow=flow.name, target=TARGET)
        result = replay(TracingDriver(app, trace), flow)
        self.assertTrue(result.ok, result.log)
        self.assertEqual('ax_window_list_empty', result.log[0]['verification_evidence'])
        self.assertEqual(1, len(app.calls)); self.assertEqual(1, app.windowless_reads)
        restored = replay(TraceDriver(trace), flow)
        self.assertTrue(restored.ok, restored.log)

    def test_normal_positive_check_still_fails_after_close_without_repeating(self):
        app = ClosingApp()
        step = Step('click', Selector(identifier='close'),
                    safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'entry'),
                    postcondition=Postcondition(contains='done'))
        result = replay(app, Flow('positive', TARGET, [step], schema=2))
        self.assertFalse(result.ok); self.assertEqual(1, len(app.calls))
        self.assertEqual(0, app.windowless_reads)
        self.assertEqual('no_window_content', result.log[-1]['error'])
