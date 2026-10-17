"""Post-boundary effects with no Mac imports or actual UI input."""
from types import SimpleNamespace
import unittest
from tacto.native_window_dispatch import UserInputState, WindowDispatchFailure, dispatch_preparation, WindowReadScope


class WindowDispatchContractTests(unittest.TestCase):
    def run_mode(self, mode):
        posted = []
        state = {'stop':mode == 'stop_before', 'clock':1001 if mode == 'expired_before' else 10,
                 'context':True, 'reads':0, 'validations':0}
        equal = lambda a, b: a == b
        binding = SimpleNamespace(point_kind='titlebar', element='window', window='window',
            _backend=SimpleNamespace(equal=equal), pid=42, window_id=17, window_bounds=(0,0,300,200), point=(150,12))
        def context():
            if not state['context']: raise RuntimeError('stale observation')
        def validate(binding):
            state['validations'] += 1
            if mode == 'binding_before' or (mode == 'binding_after' and posted): raise RuntimeError('window changed')
        def read(scope):
            scope.ready(); state['reads'] += 1
            if mode == 'read_before' or (mode == 'read_after' and posted): raise RuntimeError('input unavailable')
            return UserInputState(42 if mode == 'already_foreground' else 99 if mode == 'foreground' and posted else 77,
                (1,2) if (mode == 'pointer' and posted) or (mode == 'between_pointer' and state['reads']>=5) else (0,0),
                'other' if (mode == 'focus' and posted) or (mode == 'between_focus' and state['reads']>=5) else 'focus')
        def post(event, pid):
            self.assertEqual(pid, 42); posted.append(event)
            if mode == 'stop_after': state['stop'] = True
            if mode == 'expired_after': state['clock'] = 1001
            if mode == 'context_after': state['context'] = False
            if mode == 'post_error': raise RuntimeError('uncertain post')
        backend = SimpleNamespace(read_input=read, equal=equal, post=post,
            activation=lambda binding, timestamp: ('owner',13))
        try:
            result = dispatch_preparation(binding, deadline=1000, stopped=lambda:state['stop'],
                context=context, backend=backend, clock=lambda:state['clock'], validate=validate,
                build_pair=lambda *args:SimpleNamespace(down=1,up=2,_owners=('down','up')))
            return posted, state, result, None
        except WindowDispatchFailure as failure:
            return posted, state, None, failure

    def test_success_posts_only_to_bound_pid_and_requires_reobservation(self):
        posted, state, result, error = self.run_mode('success')
        self.assertIsNone(error)
        self.assertEqual(posted, [13,1,2])
        self.assertEqual((state['reads'],state['validations']), (10,7))
        self.assertEqual((result['unverified'], result['reobserve_required']), ('observe_effect',True))

    def test_before_and_after_failures_never_dispatch_next_event(self):
        for mode, attempted in [(m,0) for m in ('stop_before','expired_before','binding_before','read_before','already_foreground')] + [
                (m,1) for m in ('stop_after','expired_after','context_after','post_error','read_after','binding_after','foreground','pointer','focus','between_pointer','between_focus')]:
            with self.subTest(mode=mode):
                posted, state, result, error = self.run_mode(mode)
                self.assertIsNone(result)
                self.assertEqual((len(posted),error.attempted), (attempted,attempted))
                self.assertEqual(error.stopped,state['stop'])

    def test_scope_resets_timeout_and_checks_after_read(self):
        calls = []
        scope = WindowReadScope(100,lambda:calls.append('check'),clock=lambda:10)
        ax = SimpleNamespace(AXUIElementSetMessagingTimeout=lambda el,value:calls.append(('timeout',value)) or 0)
        self.assertEqual(scope.call(ax,'owned',lambda:calls.append('read') or 'value'), 'value')
        self.assertEqual(calls, ['check',('timeout',.5),'read',('timeout',0),'check'])
        calls.clear()
        def failed(): raise RuntimeError('AX call failed')
        with self.assertRaises(RuntimeError): scope.call(ax,'owned',failed)
        self.assertEqual(calls[-1],('timeout',0))
