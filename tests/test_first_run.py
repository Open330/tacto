import unittest
import re
from types import SimpleNamespace
from unittest.mock import Mock
from tacto.first_run import run_first_task, TEXT


OLD = '0 Window  Title: "Keep.txt", Document: "/owned/Keep.txt"\n  1 TextArea  Value: ORIGINAL, Identifier: First Text View\n'
NEW = '2 Window  Title: "Untitled" [focused]\n  3 TextArea  Identifier: First Text View\n'
DONE = NEW.replace('TextArea  Identifier:', 'TextArea  Value: '+TEXT.replace('\n', '\\n')+', Identifier:')


def observation(tree, **kw):
    rows = [dict(element_index=int(index), window_id=100 + int(index))
            for index in re.findall(r'^([0-9]+) Window', tree, re.M)]
    identity = dict(pid=123, process_start=123456.5, windows=rows)
    return dict(type='observation', tree=tree, window_identity=identity, **kw)


class FirstRunTests(unittest.TestCase):
    def run_case(self, *, after=OLD+NEW, final=OLD+DONE, write_error=None, create_ok=True, before=None):
        driver = Mock()
        driver.observe.side_effect = [observation(OLD) if before is None else before,
                                      observation(after) if isinstance(after, str) else after,
                                      observation(final) if isinstance(final, str) else final]
        write = SimpleNamespace(ok=False, log=[dict(status='unverified', error=write_error or 'commit_unverified',
                                  detail='missing_typed_host_commit_evidence')])
        execute = Mock(side_effect=[SimpleNamespace(ok=create_ok, log=[]), write])
        launch = Mock(return_value={'type': 'app_launch', 'status': 'launched'})
        result = run_first_task(driver, launch=launch, execute=execute)
        return result, driver, execute

    def test_visible_text_success_is_separate_from_unverified_persistence(self):
        r, driver, execute = self.run_case()
        self.assertEqual('text_verified', r['status'])
        self.assertFalse(r['persistent_commit_verified'])
        self.assertFalse(r['save_command_sent'])
        self.assertEqual(0, r['model_calls'])
        self.assertEqual(2, execute.call_count)
        flow = execute.call_args.args[1]
        self.assertEqual('set_value', flow.steps[0].action)
        self.assertEqual('Untitled', flow.steps[0].selector.ancestor)
        self.assertEqual(TEXT, flow.steps[0].params['value'])

    def test_file_picker_can_disappear_but_existing_documents_are_preserved(self):
        before = observation(OLD+'2 Window  Title: "Open" [focused]\n  3 Button  Title: New Document\n')
        before['window_identity']['windows'][-1]['window_id'] = 300
        r, _, _ = self.run_case(before=before)
        self.assertEqual('text_verified', r['status'])

    def test_unowned_or_nonempty_or_unfocused_document_is_not_written(self):
        cases = [OLD, NEW, OLD+NEW.replace('[focused]', ''),
                 OLD+NEW.replace('TextArea  Identifier:', 'TextArea  Value: USER INPUT, Identifier:'),
                 OLD+NEW.replace('"Untitled"', '"Untitled", Document: "/owned/Other.txt"'),
                 OLD+NEW+NEW]
        for after in cases:
            with self.subTest(after=after):
                r, _, execute = self.run_case(after=after)
                self.assertEqual('failed', r['status'])
                self.assertEqual(1, execute.call_count)
                self.assertIs(False, r['repeat_write'])

    def test_missing_or_truncated_observation_and_failed_new_stop_before_writing(self):
        for before in [dict(type='error', error='target_not_found'), observation(OLD, truncated=True)]:
            r, _, execute = self.run_case(before=before)
            self.assertEqual('failed', r['status']); execute.assert_not_called()
        r, _, execute = self.run_case(create_ok=False)
        self.assertEqual('new_document', r['stage']); self.assertEqual(1, execute.call_count)

    def test_failed_write_or_verification_is_never_repeated(self):
        for options in [dict(write_error='identity_changed'), dict(final=OLD+NEW),
                        dict(final=OLD+DONE.replace('Hello, World!', 'WRONG'))]:
            r, _, execute = self.run_case(**options)
            self.assertEqual('failed', r['status'])
            self.assertFalse(r['repeat_write'])
            self.assertEqual(2, execute.call_count)

    def test_launch_failure_never_touches_driver(self):
        driver = Mock()
        r = run_first_task(driver, launch=lambda _:dict(type='error', error='application_not_installed'))
        self.assertEqual('launch', r['stage']); driver.observe.assert_not_called()

    def test_autosave_renames_existing_document_without_changing_identity(self):
        renamed = OLD.replace('Keep.txt', 'Keep.rtf')
        r, _, _ = self.run_case(after=renamed+NEW, final=renamed+DONE)
        self.assertEqual('text_verified', r['status'])

    def test_new_document_can_autosave_rename_before_verification(self):
        r, _, _ = self.run_case(final=OLD+DONE.replace('"Untitled"', '"Untitled.rtf", Document: "/owned/Untitled.rtf"'))
        self.assertEqual('text_verified', r['status'])
        self.assertEqual('Untitled.rtf', r['window_title'])
        self.assertEqual(102, r['window_id'])

    def test_renamed_old_blank_document_is_not_a_new_document(self):
        before = observation(NEW)
        r, _, execute = self.run_case(before=before, after=NEW.replace('Untitled', 'Untitled.rtf'))
        self.assertEqual('new_window_not_unique', r['error'])
        self.assertEqual(1, execute.call_count)

    def test_reordered_tree_indices_keep_native_identity(self):
        after = observation(NEW.replace('2 Window', '0 Window').replace('3 TextArea', '1 TextArea')
                            + OLD.replace('0 Window', '2 Window').replace('1 TextArea', '3 TextArea'))
        after['window_identity']['windows'] = [dict(element_index=0, window_id=102), dict(element_index=2, window_id=100)]
        r, _, _ = self.run_case(after=after)
        self.assertEqual('text_verified', r['status'])

    def test_process_restart_and_missing_or_malformed_window_ids_stop_before_write(self):
        cases=[]
        for field, value in [('pid', 999), ('process_start', 123457.5), ('windows', None),
                             ('windows', [None]), ('windows', []), ('windows', [dict(element_index=0, window_id=True)])]:
            case=observation(OLD+NEW)
            case['window_identity'][field]=value
            cases.append(case)
        case=observation(OLD+NEW);case.pop('window_identity');cases.append(case)
        for after in cases:
            with self.subTest(after=after):
                r, _, execute=self.run_case(after=after)
                self.assertEqual('failed', r['status'])
                self.assertEqual(1, execute.call_count)


class WindowIdentityTransportTests(unittest.TestCase):
    def driver(self):
        from tacto.helper import HelperDriver
        driver=HelperDriver.__new__(HelperDriver)
        driver._last_action_at=0
        driver.capabilities={'observe.window_identity'}
        driver._send=Mock(return_value=observation(OLD))
        return driver

    def test_opt_in_has_no_cost_on_ordinary_observations(self):
        driver=self.driver()
        driver.observe('com.apple.TextEdit')
        self.assertNotIn('window_identity', driver._send.call_args.args[0])
        driver.observe('com.apple.TextEdit', window_identity=True)
        self.assertIs(True, driver._send.call_args.args[0]['window_identity'])

    def test_old_helper_and_invalid_values_are_refused_without_send(self):
        driver=self.driver();driver.capabilities=set()
        self.assertEqual('window_identity_unavailable', driver.observe('com.apple.TextEdit', window_identity=True)['error'])
        self.assertEqual('invalid_window_identity_request', driver.observe('com.apple.TextEdit', window_identity=1)['error'])
        driver._send.assert_not_called()


class FirstRunReadFailureTests(unittest.TestCase):
    def test_each_transport_read_failure_reports_stage_without_repeating_input(self):
        for failure_index, stage, executions in ((0, 'observe', 0), (1, 'new_document_identity', 1), (2, 'verify', 2)):
            for invalid in (OSError('PRIVATE user content'), None, {'type':'observation','tree':None}):
                with self.subTest(index=failure_index, invalid=invalid):
                    reads = [observation(OLD), observation(OLD+NEW), observation(OLD+DONE)]
                    reads[failure_index] = invalid
                    driver=Mock();driver.observe.side_effect=reads
                    execute=Mock(return_value=SimpleNamespace(ok=True, log=[]))
                    result=run_first_task(driver, launch=lambda _:dict(type='app_launch'), execute=execute)
                    self.assertEqual(('failed',stage,'observation_unavailable'),
                                     (result['status'],result['stage'],result['error']))
                    self.assertEqual(executions, execute.call_count)
                    self.assertNotIn('PRIVATE', str(result))
                    self.assertFalse(result['save_command_sent'])

    def test_uncertain_launch_is_not_retried_or_followed_by_new_document(self):
        driver=Mock();execute=Mock();launch=Mock(return_value=dict(type='error',error='launch_timeout',effect='unknown'))
        result=run_first_task(driver, launch=launch, execute=execute)
        self.assertEqual('unknown',result['effect'])
        self.assertFalse(result['repeat_write'])
        launch.assert_called_once();driver.observe.assert_not_called();execute.assert_not_called()
