import unittest
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch
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
    def setUp(self):
        presenter = patch('tacto.first_run.present_app', return_value=dict(foreground_verified=False, error='test_not_presented'))
        self.presenter = presenter.start()
        self.addCleanup(presenter.stop)
        activation = patch('tacto.first_run.activate_app', return_value=dict(foreground_verified=False))
        self.activation = activation.start()
        self.addCleanup(activation.stop)

    def test_verified_result_is_presented_and_same_window_is_observed_again(self):
        self.presenter.return_value = dict(foreground_verified=True)
        r, driver, execute = self.run_case()
        self.assertTrue(r['foreground_verified'])
        self.presenter.assert_called_once_with('com.apple.TextEdit', 123)
        self.assertEqual(2, execute.call_count)
        self.assertEqual(4, driver.observe.call_count)

    def test_old_preference_cannot_suppress_first_result(self):
        from tacto.access_log import current
        token = current.set(SimpleNamespace(preferences={"present_first_result":False}))
        try:
            self.presenter.return_value = dict(foreground_verified=True)
            r, _, execute = self.run_case()
        finally:
            current.reset(token)
        self.assertTrue(r['foreground_verified'])
        self.assertNotIn('presentation_skipped', r)
        self.presenter.assert_called_once_with('com.apple.TextEdit',123)
        self.assertEqual(2,execute.call_count)

    def test_failure_does_not_present_and_presentation_failure_does_not_repeat_input(self):
        r, _, execute = self.run_case(after=OLD)
        self.presenter.assert_not_called()
        self.assertEqual(1, execute.call_count)
        self.presenter.side_effect = OSError('PRIVATE native failure')
        r, _, execute = self.run_case()
        self.assertEqual('text_verified', r['status'])
        self.assertFalse(r['foreground_verified'])
        self.assertEqual('presentation_unavailable', r['presentation_error'])
        self.assertNotIn('PRIVATE', str(r))
        self.assertEqual(2, execute.call_count)

    def run_case(self, *, after=OLD+NEW, final=OLD+DONE, write_error=None, create_ok=True, before=None):
        driver = Mock()
        def observe(*args, **kwargs):
            if execute.call_count == 0:
                return observation(OLD) if before is None else before
            value = after if execute.call_count == 1 else final
            return observation(value) if isinstance(value, str) else value
        driver.observe.side_effect = observe
        write = SimpleNamespace(ok=False, log=[dict(status='unverified', error=write_error or 'commit_unverified',
                                  detail='missing_typed_host_commit_evidence')])
        execute = Mock(side_effect=[SimpleNamespace(ok=create_ok, log=[]), write])
        launch = Mock(return_value={'type': 'app_launch', 'status': 'launched'})
        with patch("tacto.first_run.time.sleep"):
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

    def test_delayed_new_window_is_observed_without_resending_new_or_writing_twice(self):
        driver = Mock()
        driver.observe.side_effect = [observation(OLD), observation(OLD), observation(OLD),
                                      observation(OLD+NEW), observation(OLD+DONE)]
        execute = Mock(return_value=SimpleNamespace(ok=True, log=[]))
        with patch("tacto.first_run.time.sleep") as sleep:
            result = run_first_task(driver, launch=lambda _: dict(type='app_launch'), execute=execute)
        self.assertEqual('text_verified', result['status'])
        self.assertEqual(2, execute.call_count)
        self.assertEqual(['menu', 'set_value'], [c.args[1].steps[0].action for c in execute.call_args_list])
        self.assertEqual(2, sleep.call_count)
        self.assertFalse(result['save_command_sent'])

    def test_window_identity_timeout_is_bounded_and_diagnostic_without_input_retry(self):
        result, driver, execute = self.run_case(after=OLD)
        self.assertEqual('new_window_not_unique', result['error'])
        self.assertEqual(8, result['identity_reads'])
        self.assertEqual((1, 1, 0, 0), tuple(result[k] for k in
                         ('before_document_count', 'after_document_count', 'new_document_count',
                          'missing_existing_document_count')))
        self.assertEqual(9, driver.observe.call_count)
        self.assertEqual(1, execute.call_count)
        self.assertFalse(result['repeat_write'])

    def test_file_picker_can_disappear_but_existing_documents_are_preserved(self):
        before = observation(OLD+'2 Window  Title: "Open" [focused]\n  3 Button  Title: New Document\n')
        before['window_identity']['windows'][-1]['window_id'] = 300
        r, _, _ = self.run_case(before=before)
        self.assertEqual('text_verified', r['status'])

    def test_unowned_or_nonempty_or_unfocused_document_is_not_written(self):
        cases = [OLD, OLD+NEW.replace('[focused]', ''),
                 OLD+NEW.replace('TextArea  Identifier:', 'TextArea  Value: USER INPUT, Identifier:'),
                 OLD+NEW.replace('"Untitled"', '"Untitled", Document: "/owned/Other.txt"'),
                 OLD+NEW+NEW]
        for after in cases:
            with self.subTest(after=after):
                r, _, execute = self.run_case(after=after)
                self.assertEqual('failed', r['status'])
                self.assertEqual(1, execute.call_count)
                self.assertIs(False, r['repeat_write'])

    def test_an_old_window_closing_does_not_block_the_verified_new_document(self):
        result, _, execute = self.run_case(after=NEW, final=DONE)
        self.assertEqual('text_verified', result['status'])
        self.assertEqual(102, result['window_id'])
        self.assertEqual(2, execute.call_count)

    def test_startup_picker_window_can_become_a_new_document(self):
        picker = observation(OLD+'2 Window  Title: "Open" [focused]\n  3 Button  Title: New Document\n')
        result, _, execute = self.run_case(before=picker)
        self.assertEqual('text_verified', result['status'])
        self.assertEqual(2, execute.call_count)

    def test_only_focused_new_document_is_used_when_multiple_blank_windows_appear(self):
        extra = '4 Window  Title: "Untitled 2"\n  5 TextArea  Identifier: First Text View\n'
        result, _, execute = self.run_case(after=OLD+NEW+extra, final=OLD+DONE+extra)
        self.assertEqual('text_verified', result['status'])
        self.assertEqual('Untitled', result['window_title'])
        self.assertEqual(['use_focused_new_document'], result['recovery_steps'])
        self.assertEqual(2, execute.call_count)

    def test_missing_or_truncated_observation_and_failed_new_stop_before_writing(self):
        for before in [dict(type='error', error='target_not_found'), observation(OLD, truncated=True)]:
            r, _, execute = self.run_case(before=before)
            self.assertEqual('failed', r['status']); execute.assert_not_called()
        r, _, execute = self.run_case(create_ok=False, after=OLD)
        self.assertEqual('new_document', r['stage']); self.assertEqual(1, execute.call_count)

    def recovery_case(self, reads, creation, *, activate=None):
        driver = Mock()
        driver.observe.side_effect = reads
        execute = Mock(side_effect=creation)
        activate = activate or Mock(return_value=dict(foreground_verified=True))
        with patch('tacto.first_run.time.sleep'):
            result = run_first_task(driver, launch=lambda _:dict(type='app_launch'),
                                    execute=execute, activate=activate)
        return result, execute, activate

    @staticmethod
    def refused_new():
        return SimpleNamespace(ok=False, events=[], log=[dict(status='error',
            detail=dict(error='action_failed', effect='not_started', needs_foreground=True))])

    def test_disabled_background_new_is_activated_and_completed_without_a_question(self):
        ok = SimpleNamespace(ok=True, log=[])
        result, execute, activate = self.recovery_case(
            [observation(OLD), observation(OLD), observation(OLD+NEW), observation(OLD+DONE)],
            [self.refused_new(), ok, ok])
        self.assertEqual('text_verified', result['status'])
        self.assertEqual(['activate_textedit', 'foreground_new_menu'], result['recovery_steps'])
        activate.assert_called_once_with('com.apple.TextEdit')
        self.assertEqual(['menu', 'menu', 'set_value'],
                         [c.args[1].steps[0].action for c in execute.call_args_list])

    def test_initial_background_window_read_failure_is_recovered_before_any_new_input(self):
        failed = SimpleNamespace(ok=False, events=[], log=[dict(status='error',
            stage='initial_observe', detail=dict(type='error', error='window_list_unavailable'))])
        ok = SimpleNamespace(ok=True, log=[])
        result, execute, activate = self.recovery_case(
            [observation(OLD), observation(OLD), observation(OLD+NEW), observation(OLD+DONE)],
            [failed, ok, ok])
        self.assertEqual('text_verified', result['status'])
        activate.assert_called_once_with('com.apple.TextEdit')
        self.assertEqual(3, execute.call_count)

    def test_startup_picker_uses_observed_new_document_button_instead_of_disabled_menu(self):
        for title in ('New Document', '새로운 문서', '새 문서'):
            with self.subTest(title=title):
                picker = observation(OLD+'4 Window  Title: "Open" [focused]\n  5 Button  Title: '+title+'\n')
                ok = SimpleNamespace(ok=True, log=[])
                result, execute, _ = self.recovery_case(
                    [picker, picker, observation(OLD+NEW), observation(OLD+DONE)],
                    [self.refused_new(), ok, ok])
                self.assertEqual('text_verified', result['status'])
                self.assertEqual(['menu', 'click', 'set_value'],
                                 [c.args[1].steps[0].action for c in execute.call_args_list])
                self.assertEqual(title, execute.call_args_list[1].args[1].steps[0].selector.title)

    def test_activation_created_a_document_is_reused_without_another_new_command(self):
        ok = SimpleNamespace(ok=True, log=[])
        result, execute, _ = self.recovery_case(
            [observation(OLD), observation(OLD+NEW), observation(OLD+DONE)],
            [self.refused_new(), ok])
        self.assertEqual('text_verified', result['status'])
        self.assertEqual(['menu', 'set_value'], [c.args[1].steps[0].action for c in execute.call_args_list])

    def test_new_document_verification_error_does_not_discard_an_observed_new_window(self):
        creation = SimpleNamespace(ok=False, events=[dict(state='dispatched')],
            log=[dict(status='error', error='postcondition_timeout')])
        result, execute, activate = self.recovery_case(
            [observation(OLD), observation(OLD+NEW), observation(OLD+DONE)],
            [creation, SimpleNamespace(ok=True, log=[])])
        self.assertEqual('text_verified', result['status'])
        activate.assert_not_called()
        self.assertEqual(2, execute.call_count)

    def test_transient_new_window_read_is_retried_without_repeating_creation(self):
        ok = SimpleNamespace(ok=True, log=[])
        result, execute, _ = self.recovery_case(
            [observation(OLD), dict(type='error', error='window_list_unavailable'),
             observation(OLD+NEW), observation(OLD+DONE)], [ok, ok])
        self.assertEqual('text_verified', result['status'])
        self.assertEqual(['wait_for_document_observation'], result['recovery_steps'])
        self.assertEqual(2, execute.call_count)

    def test_created_document_is_focused_automatically_before_writing(self):
        ok = SimpleNamespace(ok=True, log=[])
        result, execute, activate = self.recovery_case(
            [observation(OLD), observation(OLD+NEW.replace('[focused]', '')),
             observation(OLD+NEW), observation(OLD+DONE)], [ok, ok])
        self.assertEqual('text_verified', result['status'])
        self.assertEqual(['focus_created_document'], result['recovery_steps'])
        self.assertEqual('Untitled', activate.call_args.kwargs['window'])
        self.assertEqual(2, execute.call_count)

    def test_actual_creation_error_is_preserved_and_unknown_input_is_not_repeated(self):
        failed = SimpleNamespace(ok=False, events=[dict(state='unknown')],
            log=[dict(status='error', detail=dict(error='action_failed', effect='unknown',
                                                 detail='PRIVATE document contents'))])
        result, execute, activate = self.recovery_case(
            [observation(OLD)]*9, [failed])
        self.assertEqual(('new_document', 'action_failed'), (result['stage'], result['error']))
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(1, execute.call_count)
        activate.assert_not_called()

    def test_person_stop_is_respected_even_if_a_new_window_has_appeared(self):
        stopped = SimpleNamespace(ok=False, events=[], log=[dict(status='error', error='aborted_by_user')])
        result, execute, activate = self.recovery_case([observation(OLD)], [stopped])
        self.assertEqual('aborted_by_user', result['error'])
        self.assertEqual(1, execute.call_count)
        activate.assert_not_called()

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
