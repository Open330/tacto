"""An option reference names its original control without authorizing hidden input."""
import json
import unittest
from unittest.mock import patch
from tacto.ask import ask_here_paged


class OptionReferenceIdentityTests(unittest.TestCase):
    def test_two_control_refs_expose_owner_count_and_exact_original_scroll_candidate(self):
        options=[{'label':'項目'+str(i)+'x'*120,'visible':False,'disabled':False} for i in range(130)]
        candidates=[{'label':'decoy','role':'ComboBox','can':['select_custom_option'],'options':options},
                    {'label':'target','role':'ComboBox','can':['select_custom_option'],
                     'options':options+[{'label':'선택끝','visible':False,'disabled':False,'scroll_direction':'down'}]},
                    {'label':'owned popup','element_index':302,'role':'ScrollArea','can':['scroll']}]
        prompts=[]
        def answer(q,**kwargs):
            kwargs['_request_guard'](q.prompt,0)
            prompts.append(q.prompt)
            refs=json.loads(q.prompt.split('# 참조 원문\n',1)[1].split('\n',1)[0])
            by_pick={r['candidate_pick']:r for r in refs if r['kind']=='complete_candidate'}
            self.assertEqual(130,by_pick[0]['option_count'])
            self.assertEqual(131,by_pick[1]['option_count'])
            if len(prompts)==1:
                self.assertIn('UI 후보 페이지는 전체 옵션 목록이 아닙니다',q.prompt)
                return q.check({'read_context':{'ref':by_pick[1]['ref'],'find':'선택끝'}})
            chunk=json.loads(q.prompt.split('# 참조 내용 (관측 데이터)\n',1)[1].split('\n',1)[0])
            self.assertIn('"label": "선택끝"',chunk['text'])
            self.assertIsNone(q.check({'actions':[{'pick':1,'do':'select_custom_option','option_label':'선택끝'}]}))
            return q.check({'actions':[{'pick':2,'do':'scroll','direction':'down','pages':1}]})
        with patch('tacto.ask.ask',side_effect=answer):
            result=ask_here_paged('choose 선택끝',candidates,budget=6000,max_pages=2,
                                  token_counter=lambda text:len(text.encode())//4)
        self.assertEqual(2,len(prompts))
        self.assertEqual(candidates[2],result['actions'][0]['candidate'])
        self.assertEqual(302,result['actions'][0]['candidate']['element_index'])


if __name__=='__main__':unittest.main()
