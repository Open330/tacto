"""Where a fact is shown is asked once per screen; its value is read every time."""
import tempfile
import unittest
from unittest.mock import patch

from tacto.outcome import DONE


class WitnessMemoTests(unittest.TestCase):
    def test_send_checks_before_and_after_approval_locate_each_fact_once(self):
        from tacto.contract import Envelope
        from tacto.general import run
        from tacto.outcome import Check, OutcomeSpec
        tree = ('0 Window\n  1 Button Title: Send\n'
                '  2 StaticText Identifier: recipient, Value: bob\n'
                '  3 StaticText Identifier: body, Value: hi')
        class Screen:
            def __init__(self): self.calls, self.probes = [], []
            def observe(self, *a, **k): return {'type': 'observation', 'obs_id': 'o1', 'tree': tree}
            def identity(self, target): return target
            def probe(self, obs_id, target, idx):
                self.probes.append(idx)
                return {'type': 'probe', 'line': tree.splitlines()[idx]}
            def act(self, obs_id, target, action, **kw):
                self.calls.append(action); return {'type': 'acted'}
        spec = OutcomeSpec(required=(Check('recipient', 'request', 'before_commit', 'reports'),
                                     Check('body', 'request', 'before_commit', 'reports')),
                           expected={'recipient': 'bob', 'body': 'hi'})
        asked, approvals = [], []
        def where(name, cands):
            asked.append(name)
            c = next(c for c in cands if c['match'] == name)
            return {'pick': cands.index(c), 'candidate': c}
        d = Screen()
        run(d, 'app', 'send', lambda g, c, done: {'actions': [{'do': 'click', 'candidate': c[0]}]},
            envelope=Envelope(allows=frozenset({'transmit'})), spec=spec, ask_where=where,
            approve=lambda text: approvals.append(text) or True)
        self.assertEqual(1, len(approvals))          # the second check really ran
        self.assertEqual(['recipient', 'body'], asked)  # was 4 questions, now 2
        self.assertEqual([2, 3, 2, 3], d.probes)     # values still read both times
        self.assertEqual('click', d.calls[0]['name'])

    def test_a_located_field_that_is_gone_is_asked_for_again(self):
        from tacto.general import _witness
        asked = []
        tree_a = '0 Window Title: A\n  1 TextField Identifier: to, Value: kim'
        tree_b = '0 Window Title: A\n  1 TextField Identifier: other, Value: lee'
        class Driver:
            def __init__(self, tree): self.tree = tree
            def probe(self, obs_id, target, idx):
                return {'type': 'probe', 'line': self.tree.splitlines()[idx]}
        from tacto.general import candidates
        def where(field, readable):
            asked.append(field)
            return {'pick': 0, 'candidate': readable[0]}
        memo = {}
        for tree in (tree_a, tree_a):
            cands = candidates(tree)
            self.assertEqual('kim', _witness(Driver(tree), {'obs_id': 'o', 'tree': tree}, 't', '받는 사람',
                                             cands, where, lambda *_: None, memo))
        self.assertEqual(['받는 사람'], asked)
        cands = candidates(tree_b)
        self.assertEqual('lee', _witness(Driver(tree_b), {'obs_id': 'o', 'tree': tree_b}, 't', '받는 사람',
                                         cands, where, lambda *_: None, memo))
        self.assertEqual(['받는 사람', '받는 사람'], asked)


if __name__ == '__main__':
    unittest.main()
