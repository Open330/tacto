"""Finding a named element past the per-container cap without walking the rest."""
import unittest
from unittest import mock

from tacto import driver as native
from tacto.protocol import reveal_problem


class Node:
    def __init__(self, role, value=None, kids=()):
        self.role, self.value, self.kids = role, value, list(kids)


def folder(rows=100):
    """A window with one list of `rows` rows, each a cell holding a file name."""
    items = [Node('AXRow', kids=[Node('AXCell', kids=[Node('AXTextField', f'file{i}')])]) for i in range(rows)]
    return Node('AXWindow', 'Folder', [Node('AXOutline', kids=items)])


def attr(el, name):
    return {'AXRole': el.role, 'AXChildren': el.kids, 'AXEnabled': True}.get(name)


def label(el, quote_values=False):
    return f'Value: {el.value}' if el.value is not None else ''


def labels(el):
    return [None, None, el.value, None, None, None, None]


class WalkTests(unittest.TestCase):
    def walk(self, reveal=(), REVEAL_SCAN_NODES=5000):
        obs = native.Observation('com.apple.finder', 1)
        native._arm_reveal(obs, list(reveal) or None)
        with mock.patch.object(native, '_attr', attr), mock.patch.object(native, '_elem_label', label), \
             mock.patch.object(native, '_label_values', labels), \
             mock.patch.object(native, '_secondary_actions', lambda el: []), \
             mock.patch.object(native, 'AXUIElementSetMessagingTimeout', lambda el, t: 0), \
             mock.patch.object(native, 'REVEAL_SCAN_NODES', REVEAL_SCAN_NODES):
            obs.walk(folder())
        return obs

    def test_the_named_row_past_the_cap_is_shown_and_nothing_else(self):
        obs = self.walk(['file90'])
        text = '\n'.join(obs.lines)
        self.assertIn('Value: file90', text)
        self.assertNotIn('Value: file91', text)
        self.assertNotIn('Value: file89', text)
        self.assertIn('… (40개 더 있음; 찾는 항목 1개 표시)', text)
        self.assertEqual((1, True), (obs.revealed, obs.reveal_complete))
        # The revealed line is a real element: its index points at that node.
        line = next(l for l in obs.lines if 'file90' in l)
        self.assertEqual('file90', obs.elements[int(line.split()[0])].value)

    def test_only_exact_names_and_unchanged_without_reveal(self):
        self.assertEqual(0, self.walk(['file9']).revealed)          # file90.. are not file9
        plain = self.walk()
        self.assertIn('… (40개 더 있음)', '\n'.join(plain.lines))
        self.assertEqual(self.walk(['nothing']).lines, plain.lines)

    def test_a_scan_that_runs_out_says_so(self):
        obs = self.walk(['file99'], REVEAL_SCAN_NODES=30)
        self.assertEqual((0, False), (obs.revealed, obs.reveal_complete))

    def test_unread_depth_or_per_child_overflow_is_incomplete(self):
        with mock.patch.object(native, 'REVEAL_CHILD_DEPTH', 1):
            obs = self.walk(['file99'])        # the name sits at depth 2 of each row
        self.assertEqual((0, False), (obs.revealed, obs.reveal_complete))
        with mock.patch.object(native, 'REVEAL_CHILD_NODES', 2):
            obs = self.walk(['file99'])
        self.assertEqual((0, False), (obs.revealed, obs.reveal_complete))
        self.assertTrue(self.walk(['file99']).reveal_complete)
        self.assertEqual(40 * 3, self.walk(['nothing']).reveal_scanned)   # every row read once

    def test_numbers_match_as_text_but_booleans_do_not(self):
        self.assertEqual('7', native._reveal_text(7))
        self.assertIsNone(native._reveal_text(True))
        self.assertEqual('1.5', native._reveal_text(1.5))     # NSNumber.stringValue
        self.assertEqual('2', native._reveal_text(2.0))
        self.assertIsNone(native._reveal_text(float('nan')))

    def test_reveal_texts_are_bounded(self):
        self.assertIsNone(reveal_problem(None))
        self.assertIsNone(reveal_problem(['a'] * 8))
        for bad in ([], ['a'] * 9, [''], ['x' * 257], ['a\nb'], 'file', [3],
                    ['a\u2028b'], ['a\x85b'], ['\u200efile'], ['a\x7fb']):
            with self.subTest(bad=bad):
                self.assertIsNotNone(reveal_problem(bad))


class HelperClientTests(unittest.TestCase):
    def client(self, capabilities):
        from tacto.helper import HelperDriver
        driver = HelperDriver.__new__(HelperDriver)
        driver.capabilities = frozenset(capabilities)
        driver._last_action_at = 0
        driver.sent = []
        driver._send = lambda message: driver.sent.append(message) or {'type': 'observation', 'tree': ''}
        return driver

    def test_reveal_travels_only_to_a_helper_that_offers_it(self):
        old = self.client({'observe'})
        self.assertEqual('reveal_unavailable', old.observe('com.apple.finder', reveal=['file90'])['error'])
        self.assertEqual([], old.sent)
        new = self.client({'observe', 'observe.reveal'})
        new.observe('com.apple.finder', reveal=['file90'])
        self.assertEqual(['file90'], new.sent[0]['reveal'])
        new.observe('com.apple.finder')
        self.assertNotIn('reveal', new.sent[1])
        self.assertEqual('invalid_reveal', new.observe('com.apple.finder', reveal=['a\nb'])['error'])


class ReplayTests(unittest.TestCase):
    CAPPED = ('0 Window  Title: "Folder" [focused]\n  1 Outline\n    2 TextField  Value: file0\n'
              '    … (40개 더 있음)')

    def run_flow(self, capabilities, revealed_tree=None, complete=True):
        from tacto.flow import Flow, Step, Selector, replay
        calls = {'observe': [], 'act': []}

        class App:
            def observe(self, target, mode='tree_full', require_window=True, **kwargs):
                calls['observe'].append(kwargs.get('reveal'))
                if kwargs.get('reveal') and revealed_tree is not None:
                    return {'type': 'observation', 'obs_id': 'r', 'tree': revealed_tree,
                            'revealed': 1, 'reveal_scan_complete': complete}
                if kwargs.get('reveal'):
                    return {'type': 'observation', 'obs_id': 'r', 'tree': ReplayTests.CAPPED,
                            'revealed': 0, 'reveal_scan_complete': complete}
                return {'type': 'observation', 'obs_id': 'o', 'tree': ReplayTests.CAPPED}
            def act(self, obs_id, target, action, **kwargs):
                calls['act'].append((obs_id, action))
                return {'type': 'acted'}
            def wait_until(self, target, predicate, **kwargs):
                return self.observe(target)
        app = App(); app.capabilities = frozenset(capabilities)
        from tacto.contract import SafetyContract
        step = Step('read', Selector(role='TextField', value='file90'),
                    safety=SafetyContract(effects=['read'], reversibility='reversible'))
        result = replay(app, Flow('open', 'com.apple.finder', [step]))
        return calls, result

    def test_a_missing_named_target_is_looked_for_once_past_the_cap(self):
        tree = self.CAPPED.replace('    … (40개 더 있음)', '    7 TextField  Value: file90\n    … (40개 더 있음; 찾는 항목 1개 표시)')
        calls, result = self.run_flow({'observe', 'act', 'observe.reveal'}, tree)
        self.assertIn(['file90'], calls['observe'])
        self.assertEqual([('r', {'name': 'read', 'element_index': 7})], calls['act'])
        self.assertTrue(result.ok, result.log)

    def test_without_the_capability_nothing_more_is_asked(self):
        calls, result = self.run_flow({'observe', 'act'})
        self.assertNotIn(['file90'], calls['observe'])
        self.assertFalse(result.ok)
        self.assertEqual([], calls['act'])

    def test_an_incomplete_scan_is_named_in_the_miss(self):
        calls, result = self.run_flow({'observe', 'act', 'observe.reveal'}, complete=False)
        self.assertEqual(1, calls['observe'].count(['file90']))
        self.assertFalse(result.ok)
        self.assertIn('끝까지 확인하지 못했습니다', str(result.log))


class CommandTests(unittest.TestCase):
    def test_observe_takes_repeatable_reveal_labels(self):
        from tacto import cli
        seen = []

        class Driver:
            capabilities = frozenset({'observe', 'observe.reveal'})
            def observe(self, target, mode='tree_full', **kwargs):
                seen.append((target, kwargs.get('reveal')))
                return {'type': 'observation', 'obs_id': 'o', 'tree': '0 Window  Title: "F"'}
        with mock.patch('tacto.session.sandboxed', return_value=False), \
             mock.patch.object(cli, '_make_driver', lambda rest: Driver()), mock.patch('builtins.print'):
            self.assertEqual(0, cli.main(['observe', 'com.apple.finder', '--reveal', 'file90', '--reveal', 'file91']))
            for argv in (['observe', 'com.apple.finder', '--reveal'],
                         ['observe', 'com.apple.finder', '--reveal', 'a\nb'],
                         ['observe', 'com.apple.TextEdit', '--document', '/tmp/x', '--reveal', 'a']):
                with self.subTest(argv=argv):
                    self.assertEqual(2, cli.main(argv))
        self.assertEqual([('com.apple.finder', ['file90', 'file91'])], seen)


class TracedAndGuardedTests(unittest.TestCase):
    """The --trace path (TracingDriver) and its replay (TraceDriver), not a bypass."""
    CAPPED = ReplayTests.CAPPED
    FOUND = CAPPED.replace('    … (40개 더 있음)', '    7 TextField  Value: file90\n    … (40개 더 있음; 찾는 항목 1개 표시)')

    def app(self, complete=True):
        test = self

        class App:
            capabilities = frozenset({'observe', 'act', 'observe.reveal'})
            def __init__(self):self.reveals = [];self.acts = []
            def observe(self, target, mode='tree_full', require_window=True, **kwargs):
                self.reveals.append(kwargs.get('reveal'))
                if kwargs.get('reveal'):
                    return {'type': 'observation', 'obs_id': 'r', 'tree': test.FOUND,
                            'revealed': 1, 'reveal_scan_complete': complete, 'reveal_scanned_nodes': 10}
                return {'type': 'observation', 'obs_id': 'o', 'tree': test.CAPPED}
            def act(self, obs_id, target, action, **kwargs):
                self.acts.append((obs_id, action));return {'type': 'acted'}
            def wait_until(self, target, predicate, **kwargs):
                return self.observe(target)
        return App()

    def flow(self, effects=('read',), action='read'):
        from tacto.flow import Flow, Step, Selector
        from tacto.contract import SafetyContract, Resource
        safety = SafetyContract(effects=list(effects), reversibility='reversible',
                                resource=Resource(object_type='파일', scope='item'))
        return Flow('open', 'com.apple.finder', [Step(action, Selector(role='TextField', value='file90'), safety=safety)])

    def test_the_traced_run_reveals_records_and_replays_the_same_request(self):
        from tacto.flow import replay
        from tacto.trace import Trace, TraceDriver, TracingDriver
        inner = self.app()
        tracer = TracingDriver(inner, Trace(flow='open', target='com.apple.finder'))
        result = replay(tracer, self.flow())
        self.assertTrue(result.ok, result.log)
        self.assertIn(['file90'], inner.reveals)
        recorded = [e['request'] for e in tracer.trace.events if e['kind'] == 'observe']
        self.assertIn({'target': 'com.apple.finder', 'mode': 'tree_full', 'require_window': True,
                       'reveal': ['file90']}, recorded)
        import tempfile, os
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'trace.json')
            tracer.trace.save(path, redact=False)
            again = replay(TraceDriver(Trace.load(path)), self.flow())
        self.assertTrue(again.ok, again.log)

    def test_a_reveal_that_cannot_run_is_named_not_hidden(self):
        from tacto.flow import replay
        inner = self.app()
        inner.observe_plain = inner.observe
        def observe(target, mode='tree_full', require_window=True, **kwargs):
            if kwargs.get('reveal'):raise TypeError('unexpected keyword argument reveal')
            return inner.observe_plain(target, mode, require_window)
        inner.observe = observe
        result = replay(inner, self.flow())
        self.assertFalse(result.ok)
        self.assertIn('재확인 실패', str(result.log))

    def test_an_incomplete_scan_blocks_consequential_steps_only(self):
        from tacto.flow import replay
        read = self.app(complete=False)
        self.assertTrue(replay(read, self.flow()).ok)
        self.assertEqual(1, len(read.acts))
        delete = self.app(complete=False)
        result = replay(delete, self.flow(effects=('delete',), action='click'), approvals={0: True})
        self.assertFalse(result.ok)
        self.assertEqual('target_uniqueness_unverified', result.log[-1].get('error'))
        self.assertEqual([], delete.acts)

    def test_a_document_scoped_read_refuses_reveal(self):
        from tacto.document_scope import _refuse
        self.assertEqual('reveal_unavailable', _refuse('reveal_unavailable')['reason_code'])


if __name__ == '__main__':
    unittest.main()
