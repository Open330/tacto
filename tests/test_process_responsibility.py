"""Private API observations cannot become permission or identity proof."""
import unittest
from unittest.mock import Mock, patch
from nlc.process_responsibility import observe_responsibility


class ProcessResponsibilityTests(unittest.TestCase):
    def libraries(self, responsible=None, path=None):
        self.responsible = responsible or Mock(return_value=42)
        def read(pid, buffer, size):
            buffer.value = f'/owned/{pid}'.encode()
            return len(buffer.value)
        self.path = path or Mock(side_effect=read)
        return [Mock(responsibility_get_pid_responsible_for_pid=self.responsible),
                Mock(proc_pidpath=self.path)]

    def call(self, libraries):
        with patch('nlc.process_responsibility.sys.platform', 'darwin'), \
             patch('nlc.process_responsibility.ctypes.CDLL', side_effect=libraries):
            return observe_responsibility(17)

    def test_consistent_live_samples_are_diagnostics_only(self):
        result = self.call(self.libraries())
        self.assertEqual('observed', result['status'])
        self.assertEqual(42, result['responsible_pid'])
        self.assertEqual('/owned/17', result['process_path'])
        self.assertEqual('/owned/42', result['responsible_path'])
        self.assertFalse(result['tcc_permission_verified'])
        self.assertFalse(result['descendants_observed'])
        self.assertEqual(2, self.responsible.call_count)

    def test_changed_responsibility_or_paths_never_emit_identities(self):
        result = self.call(self.libraries(responsible=Mock(side_effect=[42, 43])))
        self.assertEqual('responsibility_changed_during_read', result['error_code'])
        self.assertIsNone(result['responsible_pid'])
        count = [0]
        def changed(pid, buffer, size):
            count[0] += 1
            buffer.value = ('/owned/a' if count[0] < 3 else '/owned/b').encode()
            return len(buffer.value)
        result = self.call(self.libraries(path=Mock(side_effect=changed)))
        self.assertEqual('responsibility_changed_during_read', result['error_code'])
        self.assertIsNone(result['process_path'])

    def test_missing_api_path_or_pid_is_unknown_without_exception_body(self):
        for error in (OSError('PRIVATE'), AttributeError('PRIVATE')):
            with self.subTest(error=type(error).__name__):
                result = self.call([error])
                self.assertEqual('responsibility_api_unavailable', result['error_code'])
                self.assertNotIn('PRIVATE', str(result))
        result = self.call(self.libraries(responsible=Mock(return_value=-1)))
        self.assertEqual('responsibility_pid_unavailable', result['error_code'])
        result = self.call(self.libraries(path=Mock(return_value=0)))
        self.assertEqual('process_path_unavailable', result['error_code'])

    def test_invalid_pid_or_platform_never_loads_native_libraries(self):
        with patch('nlc.process_responsibility.ctypes.CDLL') as load:
            for pid in (True, False, 0, -1, '42', 2**31):
                with self.subTest(pid=pid), self.assertRaises(ValueError):
                    observe_responsibility(pid)
            with patch('nlc.process_responsibility.sys.platform', 'linux'):
                result = observe_responsibility(42)
            self.assertEqual('unsupported_platform', result['error_code'])
        load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
