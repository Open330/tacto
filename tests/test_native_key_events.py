"""Construction/readback and dispatch failures without synthesizing UI input."""
from types import SimpleNamespace
import importlib.util
import unittest
from unittest.mock import patch
from tacto.native_key_events import NAVIGATION, build_window_key
from tacto.native_window_dispatch import dispatch_navigation, UserInputState, WindowDispatchFailure


class KeyBuilderTests(unittest.TestCase):
    def backend(self, corrupt=None):
        def create(kind, window, code, chars, plain, flags, timestamp):
            return object(), (kind, kind, window, code, code, chars, plain, flags, flags, False, 0)
        def read(event):
            if corrupt is None: return event
            data = list(event); data[corrupt] = 'corrupt'; return tuple(data)
        return SimpleNamespace(create=create, read=read)

    def test_all_navigation_pairs_and_invalid_inputs(self):
        for key in NAVIGATION:
            pair = build_window_key(123, key, backend=self.backend())
            self.assertEqual(pair.window_id, 123)
            self.assertEqual((pair.down[0], pair.up[0]), (10, 11))
            self.assertEqual(len(pair._owners), 2)
        for window, key in [(True, 'Tab'), (0, 'Tab'), (2**32, 'Tab'), (1, 'Escape'),
                            (1, 'cmd+Delete'), (1, '\t'), (1, None), (1, ['Tab'])]:
            with self.subTest(window=window, key=key), self.assertRaises(ValueError):
                build_window_key(window, key, backend=self.backend())

    def test_every_routing_and_payload_field_is_verified(self):
        for field in range(11):
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'readback'):
                build_window_key(123, 'Tab', backend=self.backend(corrupt=field))

    @unittest.skipUnless(importlib.util.find_spec('AppKit'), 'AppKit unavailable')
    def test_real_mac_construction_and_reflection_without_posting(self):
        for key in NAVIGATION:
            pair = build_window_key(12345, key)
            self.assertEqual(pair.key, key)


class NavigationDispatchTests(unittest.TestCase):
    def dispatch(self, failure=None):
        posted, routing_calls = [], []
        state = {'stopped': False, 'clock': 10}
        binding = SimpleNamespace(point_kind='element_center', element='input', window='window',
            window_id=123, pid=42, _backend=SimpleNamespace(equal=lambda a,b:a==b))
        def routing(binding, *, element):
            routing_calls.append(element)
            if failure == 'target_before' or (failure == 'target_after' and posted):
                raise RuntimeError('target responder/window changed')
        def post(event, pid):
            self.assertEqual(pid, 42); posted.append(event)
            if failure == 'stop_after': state['stopped'] = True
            if failure == 'expired_after': state['clock'] = 1001
            if failure == 'post_error': raise RuntimeError('unknown event effect')
        def preflight():
            state['preflights'] = state.get('preflights', 0) + 1
            self.assertFalse(posted, 'lexical precondition must precede key-down')
            if failure == 'preflight': raise RuntimeError('initial expectation changed')
        def read(scope):
            scope.ready()
            return UserInputState(77, (1,2) if posted and failure=='pointer' else (0,0), 'user-focus')
        backend = SimpleNamespace(read_input=read, equal=lambda a,b:a==b, post=post)
        try:
            result = dispatch_navigation(binding, 'Tab', deadline=1000, stopped=lambda:state['stopped'],
                context=lambda:None, clock=lambda:state['clock'], backend=backend,
                validate=lambda binding:None, routing=routing,
                build_pair=lambda *args:SimpleNamespace(down=10,up=11,_owners=(object(),object())),
                preflight=preflight)
            self.assertEqual(state['preflights'], 1)
            return posted, routing_calls, result, None
        except WindowDispatchFailure as error:
            return posted, routing_calls, None, error

    def test_tab_allows_new_responder_in_same_window_for_key_up(self):
        posted, routing, result, error = self.dispatch()
        self.assertIsNone(error)
        self.assertEqual(posted, [10,11])
        self.assertEqual(routing, [True,True,False,False,False])
        self.assertEqual(result['input_route'], 'window_key')
        self.assertTrue(result['reobserve_required'])

    def test_uncertain_down_never_sends_a_compensating_or_next_event(self):
        for failure, attempts in [('preflight',0), ('target_before',0), ('target_after',1), ('stop_after',1),
                                  ('expired_after',1), ('post_error',1), ('pointer',1)]:
            with self.subTest(failure=failure):
                posted, routing, result, error = self.dispatch(failure)
                self.assertIsNone(result)
                self.assertEqual((len(posted), error.attempted), (attempts, attempts))
                self.assertEqual(error.stopped, failure=='stop_after')


@unittest.skipUnless(importlib.util.find_spec('CoreFoundation'), 'CoreFoundation unavailable')
class TargetRoutingTests(unittest.TestCase):
    def test_actual_routing_checks_identity_ancestry_type_and_current_input(self):
        from tacto.native_window_binding import _MacBackend, NativeWindowBindingError
        nodes = {'first': {'AXRole':'AXTextArea','AXParent':'window'},
                 'second': {'AXRole':'AXTextArea','AXParent':'window'},
                 'foreign': {'AXRole':'AXTextArea','AXParent':'other-window'},
                 'window': {'AXRole':'AXWindow'}, 'other-window':{'AXRole':'AXWindow'}}
        state = {'window':'window','input':'first','cross_pid':False}
        def attr(element, key):
            if element == 'app': return state['window' if key=='AXFocusedWindow' else 'input']
            return nodes[element][key]
        backend = SimpleNamespace(attr=attr, equal=lambda a,b:a==b,
            ax=SimpleNamespace(AXUIElementCreateApplication=lambda pid:'app', AXUIElementGetTypeID=lambda:9),
            pid=lambda el:99 if state['cross_pid'] and el=='first' else 42,
            require_ready=lambda window,pid:None)
        binding = SimpleNamespace(window='window', element='first', pid=42)
        with patch('CoreFoundation.CFGetTypeID', side_effect=lambda value:0 if value is None else 9):
            _MacBackend.require_key_target(backend, binding, element=True)
            state['input'] = 'second'
            _MacBackend.require_key_target(backend, binding, element=False)
            with self.assertRaises(NativeWindowBindingError):
                _MacBackend.require_key_target(backend, binding, element=True)
            for window, focused, cross in [('other-window','first',False),
                    ('window','foreign',False), ('window',None,False), ('window','first',True)]:
                state.update(window=window, input=focused, cross_pid=cross)
                with self.subTest(state=state), self.assertRaises(NativeWindowBindingError):
                    _MacBackend.require_key_target(backend, binding, element=False)
