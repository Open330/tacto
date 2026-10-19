"""An open select and a row that scrolls it share the first candidate page."""
import json
import unittest
from unittest.mock import patch

from tacto.ask import _open_select_order, ask_here_paged

OWNER = 'nlc-fixture-choice'


def screen(rows=60, visible=8):
    """The shape of the 131-option native run: one open select, one row each."""
    labels = ['Alpha', 'Beta', 'Gamma'] + [f'Choice{i:03d}' for i in range(128)]
    options = [{'label': label, 'visible': index < visible, 'disabled': False,
                **({} if index < visible else {'scroll_direction': 'down'})}
               for index, label in enumerate(labels)]
    candidates = [
        {'element_index': 0, 'role': 'Window', 'label': 'Fixture', 'can': ['prepare_window']},
        {'element_index': 1, 'role': 'ComboBox', 'label': 'Alpha', 'match': OWNER,
         'attr': 'Identifier', 'container': '', 'can': ['read', 'scroll', 'select_custom_option'],
         'disabled': False, 'options': options},
        {'element_index': 2, 'role': 'Button', 'label': OWNER, 'container': OWNER, 'can': ['click']},
    ]
    for index in range(rows):
        candidates.append({'element_index': 100 + index, 'role': 'TextField',
                           'label': labels[index], 'container': OWNER,
                           'can': ['click', 'read', 'scroll'], 'disabled': False})
    return candidates


def counter(text):
    return len(text.encode()) // 4


def native_screen():
    candidates=screen()
    candidates[1].update(can=['read','scroll','press_key','select_custom_option'],
        navigation_keys=['Down','Left','Right','Tab','Up','shift+Tab'],
        where='창: NLC Native Select Fixture',line='  1 ComboBox  Value: Alpha, Identifier: '+OWNER)
    for row in candidates[3:]:
        row.update(can=['click','set_value','read','scroll','press_key','submit'],
            navigation_keys=['Down','Left','Right','Tab','Up','shift+Tab'],
            match=row['label'],attr='Value',where='안: '+OWNER+' / 창: NLC Native Select Fixture')
    return candidates


class OpenSelectOrderTests(unittest.TestCase):
    def test_select_and_first_matching_row_lead_and_nothing_is_dropped(self):
        candidates = screen()
        order = _open_select_order(candidates)
        self.assertEqual([1, 3], order[:2])
        self.assertEqual(sorted(order), list(range(len(candidates))))
        self.assertEqual([0, 2, *range(4, len(candidates))], order[2:])

    def test_anything_ambiguous_or_unsupported_keeps_the_original_order(self):
        def changed(edit):
            candidates = screen()
            edit(candidates)
            return _open_select_order(candidates)
        cases = {
            'closed select': lambda c: c[1].update(can=['open_select'], options=[]),
            'no identifier': lambda c: c[1].update(attr='Title'),
            'second select same owner': lambda c: c.append({**c[1], 'element_index': 9}),
            'second open select': lambda c: c.append({**c[1], 'match': 'other'}),
            'rows of another popup': lambda c: [r.update(container='other') for r in c[3:]],
            'rows cannot scroll': lambda c: [r.update(can=['click']) for r in c[3:]],
            'rows disabled': lambda c: [r.update(disabled=True) for r in c[3:]],
            'no visible option': lambda c: [o.update(visible=False) for o in c[1]['options']],
            'duplicate row labels': lambda c: [r.update(label='Alpha') for r in c[3:]],
            'duplicate visible options': lambda c: [o.update(label='Same') for o in c[1]['options']]
                                                  + [r.update(label='Same') for r in c[3:]],
        }
        for name, edit in cases.items():
            with self.subTest(name):
                self.assertIsNone(changed(edit))
        self.assertEqual(4, changed(lambda c: c[3].update(disabled=True))[1])


class OpenSelectPagingTests(unittest.TestCase):
    def test_native_action_fields_fit_select_and_scroll_at_2500_o200k_tokens(self):
        import tiktoken
        candidates=native_screen()
        encoding=tiktoken.get_encoding('o200k_base')
        prompts=[]
        def answer(q,**kwargs):
            kwargs['_request_guard'](q.prompt,0)
            prompts.append(q.prompt)
            self.assertLessEqual(len(encoding.encode(q.prompt,disallowed_special=())),2500)
            block=q.prompt.split('# 후보 페이지\n',1)[1].split('\n',1)[0]
            picks=json.loads(block.split('번호 ',1)[1].split('.',1)[0])
            self.assertIn(1,picks);self.assertIn(3,picks)
            if len(prompts)==1:
                refs=json.loads(q.prompt.split('# 참조 원문\n',1)[1].split('\n',1)[0])
                ref=next(r['ref'] for r in refs if r.get('candidate_pick')==1)
                return q.check({'read_context':{'ref':ref,'find':'Choice127'}})
            return q.check({'actions':[{'pick':3,'do':'scroll','direction':'down','pages':10}]})
        with patch('tacto.ask.ask',side_effect=answer):
            result=ask_here_paged('선택 메뉴에서 Choice127를 한 번 선택하고, 결과 표시의 값을 읽어 알려주세요.',
                candidates,budget=2500,max_pages=8,
                token_counter=lambda text:len(encoding.encode(text,disallowed_special=())))
        self.assertEqual(2,len(prompts))
        self.assertEqual(candidates[3],result['actions'][0]['candidate'])

    def test_repeated_reference_reads_do_not_submit_an_oversized_prompt(self):
        import tiktoken
        encoding=tiktoken.get_encoding('o200k_base');prompts=[]
        def answer(q,**kwargs):
            kwargs['_request_guard'](q.prompt,0)
            prompts.append(q.prompt)
            self.assertLessEqual(len(encoding.encode(q.prompt,disallowed_special=())),2500)
            if len(prompts)<=4:
                refs=json.loads(q.prompt.split('# 참조 원문\n',1)[1].split('\n',1)[0])
                ref=next(r['ref'] for r in refs if r.get('candidate_pick')==1)
                return q.check({'read_context':{'ref':ref,'find':f'Choice{128-len(prompts):03d}',
                                               'length':200}})
            return q.check({'actions':[{'pick':3,'do':'scroll','direction':'down','pages':10}]})
        with patch('tacto.ask.ask',side_effect=answer):
            result=ask_here_paged('choose Choice127',native_screen(),budget=2500,max_pages=8,
                token_counter=lambda text:len(encoding.encode(text,disallowed_special=())))
        self.assertLessEqual(len(prompts),5)
        if result.get('stop_reason'):
            self.assertEqual('context_budget_overflow',result['stop_reason'])
            self.assertEqual([],result['actions'])
        else:
            self.assertEqual('scroll',result['actions'][0]['do'])

    def test_found_option_scrolls_from_the_first_page_with_the_original_candidate(self):
        candidates = screen()
        prompts = []
        def answer(q, **kwargs):
            kwargs['_request_guard'](q.prompt, 0)
            prompts.append(q.prompt)
            self.assertLessEqual(counter(q.prompt), 2800)
            self.assertIn('다음 페이지: 있음', q.prompt)
            refs = json.loads(q.prompt.split('# 참조 원문\n', 1)[1].split('\n', 1)[0])
            ref = next(r['ref'] for r in refs if r.get('candidate_pick') == 1)
            if len(prompts) == 1:
                return q.check({'read_context': {'ref': ref, 'find': 'Choice127'}})
            # Found but hidden: selecting it is still refused.
            self.assertIsNone(q.check({'actions': [{'pick': 1, 'do': 'select_custom_option',
                                                    'option_label': 'Choice127'}]}))
            # A row outside this page cannot be acted on either.
            self.assertIsNone(q.check({'actions': [{'pick': 62, 'do': 'scroll',
                                                    'direction': 'down', 'pages': 10}]}))
            return q.check({'actions': [{'pick': 3, 'do': 'scroll', 'direction': 'down',
                                         'pages': 10}]})
        with patch('tacto.ask.ask', side_effect=answer):
            result = ask_here_paged('choose Choice127', candidates, budget=2800, max_pages=8,
                                    token_counter=counter)
        self.assertEqual(2, len(prompts))
        self.assertEqual(candidates[3], result['actions'][0]['candidate'])

    def test_sequential_pages_still_show_every_candidate_once(self):
        candidates = screen()
        shown = []
        def answer(q, **kwargs):
            kwargs['_request_guard'](q.prompt, 0)
            self.assertLessEqual(counter(q.prompt), 2000)
            block = q.prompt.split('# 후보 페이지\n', 1)[1].split('\n', 1)[0]
            picks = json.loads(block.split('번호 ', 1)[1].split('.', 1)[0])
            shown.extend(picks)
            if '다음 페이지: 있음' in block:
                return q.check({'done': False, 'actions': [], 'next_page': True})
            return q.check({'done': True, 'actions': []})
        with patch('tacto.ask.ask', side_effect=answer):
            ask_here_paged('look', candidates, budget=2000, max_pages=32, token_counter=counter)
        self.assertEqual(_open_select_order(candidates), shown)
        self.assertEqual(list(range(len(candidates))), sorted(shown))


if __name__ == '__main__':
    unittest.main()
