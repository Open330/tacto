"""Model-loop rows named only by their text, and Return as a declared submit."""
import unittest

from tacto.general import candidates, reacquire, run

CAPS = {'action.double_click', 'action.press_key', 'press_key.element_bound',
        'press_key.return_bound'}
OPENED = {'type': 'acted', 'unverified': 'observe_effect', 'reobserve_required': True}


def chat_list(names, *, field=True):
    rows = ''.join(f'\n    {2 + 2 * k} Row\n      {3 + 2 * k} StaticText Value: {n}'
                   for k, n in enumerate(names))
    tail = (f'\n  {2 + 2 * len(names)} TextArea Description: 메시지 입력'
            if field else '')
    return f'0 Window Title: 채팅\n  1 Table Identifier: chats{rows}{tail}'


class App:
    capabilities = CAPS

    def __init__(self, names, *, reorder_to=None):
        self.names, self.reorder_to = list(names), reorder_to
        self.calls, self.kwargs, self.opened = [], [], None
        self.observations = 0

    def observe(self, *args, **kwargs):
        self.observations += 1
        if self.opened:
            tree = f'0 Window Title: {self.opened} 대화\n  1 TextArea Description: 메시지 입력'
        else:
            tree = chat_list(self.names)
        return {'type': 'observation', 'obs_id': f'obs{self.observations}', 'tree': tree}

    def act(self, obs_id, target, action, **kwargs):
        self.calls.append(dict(action))
        self.kwargs.append(kwargs)
        line = next(l for l in chat_list(self.names).splitlines()
                    if l.strip().startswith(f"{action['element_index']} "))
        assert ' Row' in line
        row = chat_list(self.names).splitlines()
        text = row[row.index(line) + 1].split('Value: ')[1]
        self.opened = text
        if action['name'] == 'press_key':
            return {**OPENED, 'input_route': 'window_bound_submit_key', 'posted_events': 1}
        return {**OPENED, 'input_route': 'window_bound_double_click', 'posted_events': 4}

    def wait_until(self, target, predicate, **kwargs):
        tree = self.observe()['tree']
        if predicate(tree):
            return {'type': 'observation', 'tree': tree}
        return {'type': 'error', 'error': 'timeout'}


class RowCandidateTests(unittest.TestCase):
    def test_unlabeled_row_is_named_and_found_by_its_text(self):
        rows = [c for c in candidates(chat_list(['홍길동', '김철수']), capabilities=CAPS)
                if c['role'] == 'Row']
        self.assertEqual(['홍길동', '김철수'], [c['label'] for c in rows])
        self.assertEqual({'role': 'StaticText', 'attr': 'Value', 'match': '홍길동'},
                         rows[0]['via_child'])
        self.assertIn('double_click', rows[0]['can'])
        self.assertIn('submit', rows[0]['can'])
        # Re-found by text after the list reorders: a new message moved it.
        self.assertEqual(4, reacquire(rows[0], chat_list(['김철수', '홍길동'])))
        self.assertIsNone(reacquire(rows[0], chat_list(['김철수'])))

    def test_rows_whose_text_does_not_tell_them_apart_are_not_offered(self):
        rows = [c for c in candidates(chat_list(['홍길동', '홍길동', '김철수']), capabilities=CAPS)
                if c['role'] == 'Row']
        self.assertEqual(['김철수'], [c['label'] for c in rows])

    def test_submit_needs_the_bound_return_capability_and_a_writable_field(self):
        tree = chat_list(['홍길동'])
        field = next(c for c in candidates(tree, capabilities=CAPS) if c['role'] == 'TextArea')
        self.assertIn('submit', field['can'])
        for caps in (CAPS - {'press_key.return_bound'}, CAPS - {'press_key.element_bound'}):
            field = next(c for c in candidates(tree, capabilities=caps) if c['role'] == 'TextArea')
            self.assertNotIn('submit', field['can'])
        readonly = tree.replace('TextArea Description', 'TextArea [readonly] Description')
        field = next(c for c in candidates(readonly, capabilities=CAPS) if c['role'] == 'TextArea')
        self.assertNotIn('submit', field['can'])


class RowLoopTests(unittest.TestCase):
    def plan(self, do, name='홍길동', expect='홍길동 대화'):
        def planner(goal, cands, history):
            if any(c['role'] == 'Row' for c in cands):
                row = next(c for c in cands if c['label'] == name)
                return {'actions': [{'do': do, 'candidate': row}], 'expect': expect}
            return {'done': True}
        return planner

    def test_double_click_acts_on_the_row_whose_text_matches_now(self):
        app = App(['홍길동', '김철수'])
        observe = app.observe
        def reorder(*args, **kwargs):
            # The list moves between planning and acting.
            if app.observations == 1:
                app.names = ['김철수', '홍길동']
            return observe(*args, **kwargs)
        app.observe = reorder
        result = run(app, 'chat', '홍길동 채팅방 열어줘', self.plan('double_click'))
        self.assertEqual([{'name': 'double_click', 'element_index': 4}], app.calls)
        self.assertEqual('홍길동', app.opened)
        self.assertTrue(result.ok, result.why)
        # The row has no name of its own to check; its role still is.
        self.assertEqual('Row', app.kwargs[0]['expect']['role'])
        self.assertNotIn('label', app.kwargs[0]['expect'])

    def test_row_that_cannot_be_found_again_is_not_acted_on(self):
        app = App(['홍길동', '김철수'])
        observe = app.observe
        def vanish(*args, **kwargs):
            if app.observations == 1:
                app.names = ['김철수']
            return observe(*args, **kwargs)
        app.observe = vanish
        result = run(app, 'chat', '홍길동 채팅방 열어줘', self.plan('double_click'))
        self.assertEqual([], app.calls)
        self.assertIn('다시 찾지 못했습니다', result.why)

    def test_return_on_a_row_opens_it_with_submit_authorization(self):
        app = App(['홍길동', '김철수'])
        result = run(app, 'chat', '홍길동 채팅방 열어줘', self.plan('submit'))
        self.assertEqual([{'name': 'press_key', 'key': 'Return', 'element_index': 2}], app.calls)
        self.assertIs(True, app.kwargs[0]['submit_authorized'])
        self.assertEqual('홍길동', app.opened)
        self.assertTrue(result.ok, result.why)
        # The submit ended the batch, so its expected text is the evidence.
        self.assertTrue(result.verified, result.why)
        self.assertEqual('confirmed', result.events[0]['state'])

    def test_return_without_new_expected_text_is_not_pressed(self):
        for expect in (None, '채팅'):  # absent, or already on screen
            with self.subTest(expect=expect):
                app = App(['홍길동'])
                result = run(app, 'chat', '홍길동 채팅방 열어줘', self.plan('submit', expect=expect))
                self.assertEqual([], app.calls)
                self.assertIn('Return', result.why)

    def test_return_in_a_message_field_is_transmit_and_needs_host_evidence(self):
        app = App(['홍길동'])
        def planner(goal, cands, history):
            field = next(c for c in cands if c['role'] == 'TextArea')
            return {'actions': [{'do': 'submit', 'candidate': field}], 'expect': '보냈습니다'}
        approvals = []
        result = run(app, 'chat', '홍길동에게 메시지 보내줘', planner,
                     approve=lambda text: approvals.append(text) or True)
        # No outcome spec, no receipt: a send is not dispatched on the
        # model's word, whatever the approval callback would have said.
        self.assertEqual([], app.calls)
        self.assertFalse(result.ok)


class PlannerCheckTests(unittest.TestCase):
    def check(self, answer, cands):
        from tacto.ask import what_to_do_here
        return what_to_do_here('goal', cands).check(answer)

    def test_submit_must_name_new_text_and_end_the_batch(self):
        cands = candidates(chat_list(['홍길동', '김철수']), capabilities=CAPS)
        row = next(i for i, c in enumerate(cands) if c['label'] == '홍길동')
        field = next(i for i, c in enumerate(cands) if c['role'] == 'TextArea')
        self.assertIsNone(self.check({'actions': [{'pick': field, 'do': 'submit'}]}, cands))
        ok = self.check({'actions': [{'pick': field, 'do': 'submit'}], 'expect': '보냄'}, cands)
        self.assertEqual('submit', ok['actions'][0]['do'])
        self.assertIsNone(self.check({'actions': [{'pick': field, 'do': 'submit'},
                                                  {'pick': row, 'do': 'click'}], 'expect': 'x'}, cands))
        # A row found by its text is re-found just before acting: last.
        self.assertIsNone(self.check({'actions': [{'pick': row, 'do': 'click'},
                                                  {'pick': field, 'do': 'click'}]}, cands))
        self.assertIsNone(self.check({'actions': [{'pick': field, 'do': 'press_key',
                                                   'key': 'Return'}]}, cands))


class PromotionTests(unittest.TestCase):
    def test_text_named_row_promotes_to_a_click_ancestor_step(self):
        from tacto.promote import Candidate, to_flow
        row = next(c for c in candidates(chat_list(['홍길동']), capabilities=CAPS)
                   if c['role'] == 'Row')
        flow = to_flow(Candidate(goal='g', target='chat', quote='q', screens=[],
                                 actions=[{**row, 'do': 'double_click'}], explored=[],
                                 envelope=None, verified=True), 'open_chat')
        sel = flow.steps[0].selector
        self.assertEqual(('StaticText', '홍길동', ['Row']), (sel.role, sel.value, sel.click_ancestor))
        self.assertEqual(2, sel.resolve(chat_list(['홍길동']))[0])


if __name__ == '__main__':
    unittest.main()
