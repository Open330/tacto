"""Mock-only native capture contracts; these tests do not capture the desktop."""
from dataclasses import replace
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest

from tacto.native_visual import (NativeVisualError, NativeWindowBinding, WindowRecord,
                               capture_native, _await_callback)

PNG = b'\x89PNG\r\n\x1a\nmock-image'


class Backend:
    allowed = True
    def __init__(self):
        self.element = object()
        self.state = ('AXWindow', 'Document', (10, 20, 300, 200), 41)
        self.record = WindowRecord(72, 41, 'Document', (10, 20, 300, 200), True, object())
        self.records = [self.record]
        self.calls = []
        self.on_capture = lambda: None
    def permission(self):
        self.calls.append('permission')
        return self.allowed
    def ax_state(self, element):
        return self.state
    def windows(self, timeout):
        self.calls.append('windows')
        return list(self.records)
    def screenshot(self, window, timeout):
        self.calls.append(('screenshot', window.window_id))
        self.on_capture()
        return PNG, (300, 200)


class NativeCaptureContract(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.obs = SimpleNamespace(obs_id='o1', target='Editor', pid=41,
                                   elements=[self.backend.element])
        self.driver = SimpleNamespace(_latest={'editor': self.obs})
    def capture(self, **kwargs):
        return capture_native(self.driver, 'o1', 'Editor', backend=self.backend, **kwargs)

    def test_unique_window_correlation_and_private_metadata(self):
        capture = self.capture()
        self.assertEqual(72, capture.window_id)
        self.assertEqual('best_effort_unique_window_correlation', capture.binding)
        self.assertEqual('window_pixels', capture.to_dict()['coordinate_space'])
        self.assertEqual([('screenshot', 72)], [c for c in self.backend.calls if isinstance(c, tuple)])
        self.assertNotIn('png', capture.to_dict())
        self.assertNotIn('mock-image', repr(capture))
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save(Path(directory) / 'private.png')
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(PNG, path.read_bytes())

    def test_no_permission_has_no_enumeration_capture_or_prompt(self):
        self.backend.allowed = False
        with self.assertRaisesRegex(NativeVisualError, 'permission_missing'):
            self.capture()
        self.assertEqual(['permission'], self.backend.calls)

    def test_multiple_ax_windows_require_explicit_index(self):
        self.obs.elements.append(object())
        with self.assertRaisesRegex(NativeVisualError, 'ambiguous_or_missing_ax_window'):
            self.capture()
        self.assertEqual(1, self.capture(window_index=1).window_index)

    def test_duplicate_sck_match_refused_even_if_ids_differ(self):
        self.backend.records.append(replace(self.backend.record, window_id=73))
        with self.assertRaisesRegex(NativeVisualError, 'ambiguous_or_missing_sc_window'):
            self.capture()
        self.assertFalse(any(isinstance(c, tuple) for c in self.backend.calls))

    def test_host_binding_pins_id_and_is_rechecked(self):
        self.backend.records.append(replace(self.backend.record, window_id=73))
        binding = NativeWindowBinding('o1', 'Editor', 41, 73)
        self.driver.native_window_binding = lambda *args: binding
        self.assertEqual(73, self.capture().window_id)
        self.backend.on_capture = lambda: setattr(self.driver, 'native_window_binding', lambda *args: replace(binding, window_id=72))
        with self.assertRaisesRegex(NativeVisualError, 'native_window_binding_changed'):
            self.capture()
        self.backend.on_capture = lambda: None
        self.assertEqual(72, self.capture().window_id)

    def test_wrong_pid_title_bounds_and_offscreen_refused(self):
        for change in ({'pid': 99}, {'title': 'Other'}, {'frame': (11, 20, 300, 200)}, {'on_screen': False}):
            self.backend.records = [replace(self.backend.record, **change)]
            with self.subTest(change=change), self.assertRaisesRegex(NativeVisualError, 'ambiguous_or_missing_sc_window'):
                self.capture()
        self.backend.state = (*self.backend.state[:3], 99)
        with self.assertRaisesRegex(NativeVisualError, 'ax_window_pid_mismatch'):
            self.capture()

    def test_changed_window_id_and_ax_handle_after_capture_refused(self):
        self.backend.on_capture = lambda: setattr(self.backend, 'records', [replace(self.backend.record, window_id=73)])
        with self.assertRaisesRegex(NativeVisualError, 'native_window_changed'):
            self.capture()
        self.backend.records = [self.backend.record]
        self.backend.on_capture = lambda: self.obs.elements.__setitem__(0, object())
        with self.assertRaisesRegex(NativeVisualError, 'native_window_changed'):
            self.capture()

    def test_observation_replaced_during_capture_refused(self):
        self.backend.on_capture = lambda: setattr(self.obs, 'obs_id', 'o2')
        with self.assertRaisesRegex(NativeVisualError, 'stale_observation'):
            self.capture()

    def test_window_metadata_changed_during_capture_refused(self):
        self.backend.on_capture = lambda: setattr(self.backend, 'state', ('AXWindow', 'Other', (10, 20, 300, 200), 41))
        with self.assertRaisesRegex(NativeVisualError, 'native_window_changed'):
            self.capture()

    def test_missing_title_and_bad_timeout_refused(self):
        for timeout in (True, 0, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.capture(timeout_seconds=timeout)
        self.backend.state = ('AXWindow', None, (10, 20, 300, 200), 41)
        with self.assertRaisesRegex(NativeVisualError, 'insufficient_ax_window_identity'):
            self.capture()


class NativeCallbackContract(unittest.TestCase):
    def test_callback_delivered_by_pumped_runloop(self):
        callbacks = []
        result = _await_callback(callbacks.append, 1, lambda seconds: callbacks[0]('image', None))
        self.assertEqual('image', result)

    def test_timeout_late_callback_discarded(self):
        callbacks = []
        with self.assertRaisesRegex(NativeVisualError, 'capture_timeout'):
            _await_callback(callbacks.append, 0, lambda seconds: None)
        callbacks[0]('late image', None)  # no mutation of closed result/no error
        self.assertEqual('new image', _await_callback(lambda cb: cb('new image', None), 1, lambda s: None))

    def test_callback_error_refuses_image(self):
        with self.assertRaisesRegex(NativeVisualError, 'screen_capture_failed'):
            _await_callback(lambda cb: cb('image', 'error'), 1, lambda s: None)

class ScreenCaptureAPIContract(unittest.TestCase):
    def test_backend_uses_only_desktop_independent_window_filter(self):
        from unittest.mock import Mock, patch
        from tacto.native_visual import ScreenCaptureBackend
        backend = ScreenCaptureBackend.__new__(ScreenCaptureBackend)
        sc = Mock()
        backend.sc = sc
        backend.q = SimpleNamespace(CGImageGetWidth=lambda image: 300,
                                    CGImageGetHeight=lambda image: 200)
        backend._pump = lambda seconds: None
        sc.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_.side_effect = lambda content_filter, config, cb: cb('image', None)
        bitmap = Mock()
        bitmap.representationUsingType_properties_.return_value = PNG
        kit = Mock()
        kit.NSBitmapImageRep.alloc.return_value.initWithCGImage_.return_value = bitmap
        handle = object()
        window = WindowRecord(72, 41, 'Document', (10, 20, 300, 200), True, handle)
        with patch.dict('sys.modules', {'AppKit': kit}):
            png, size = backend.screenshot(window, 1)
        self.assertEqual((PNG, (300, 200)), (png, size))
        sc.SCContentFilter.alloc.return_value.initWithDesktopIndependentWindow_.assert_called_once_with(handle)
        config = sc.SCStreamConfiguration.alloc.return_value.init.return_value
        config.setIncludeChildWindows_.assert_called_once_with(False)
        config.setShowsCursor_.assert_called_once_with(False)
        config.setIgnoreShadowsSingleWindow_.assert_called_once_with(True)
        config.setWidth_.assert_called_once_with(300)
        config.setHeight_.assert_called_once_with(200)

    def test_unavailable_screenshot_manager_refused(self):
        from unittest.mock import Mock, patch
        from tacto.native_visual import ScreenCaptureBackend
        backend = ScreenCaptureBackend.__new__(ScreenCaptureBackend)
        backend.sc = None
        backend.f = Mock()
        backend.f.NSProcessInfo.processInfo.return_value.operatingSystemVersion.return_value = SimpleNamespace(majorVersion=13)
        with patch.dict('sys.modules', {'ScreenCaptureKit': SimpleNamespace()}):
            with self.assertRaisesRegex(NativeVisualError, 'screenshot_manager_unavailable'):
                backend._framework()

    def test_tuple_os_version_is_supported(self):
        from unittest.mock import Mock, patch
        from tacto.native_visual import ScreenCaptureBackend
        backend = ScreenCaptureBackend.__new__(ScreenCaptureBackend)
        backend.sc = None
        backend.f = Mock()
        backend.f.NSProcessInfo.processInfo.return_value.operatingSystemVersion.return_value = (27, 0, 0)
        framework = SimpleNamespace(SCScreenshotManager=SimpleNamespace(
            captureImageWithFilter_configuration_completionHandler_=lambda *args: None))
        with patch.dict('sys.modules', {'ScreenCaptureKit': framework}):
            self.assertIs(framework, backend._framework())
