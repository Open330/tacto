import json
import unittest
from unittest.mock import patch
from tacto.text_paging import page_text, text_ref
from tacto.ask import ask_here_paged


class TextPagingTests(unittest.TestCase):
    def test_unicode_reassembly_and_full_prompt_budget(self):
        text=('한글😀e\u0301\\\"\n'*200)+'END'
        ref=text_ref(text,'snapshot')
        offset=0;pieces=[]
        while offset<len(text):
            page=page_text(text,ref=ref,context='snapshot',offset=offset,budget=240,
                render=lambda part,start,end,total:'instruction '+json.dumps([start,end,total,part],ensure_ascii=False))
            self.assertFalse(page.overflow)
            self.assertLessEqual(len(page.prompt.encode()),240)
            self.assertGreater(page.end,offset)
            pieces.append(page.text);offset=page.end
        self.assertEqual(''.join(pieces),text)
        with self.assertRaises(ValueError):page_text(text+'changed',ref=ref,context='snapshot',budget=240,render=lambda *a:'')
        with self.assertRaises(ValueError):page_text(text,ref=ref,context='other',budget=240,render=lambda *a:'')
        for offset in (-1,True,len(text)+1):
            with self.assertRaises(ValueError):page_text(text,ref=ref,context='snapshot',offset=offset,budget=240,render=lambda *a:'')

    def test_oversized_overhead_and_seek_are_explicit(self):
        text='abc😀END';ref=text_ref(text)
        page=page_text(text,ref=ref,offset=3,budget=100,render=lambda s,*_:s)
        self.assertEqual(page.text,'😀END')
        page=page_text(text,ref=ref,budget=1,render=lambda s,*_:'overhead'+s)
        self.assertTrue(page.overflow)

    def test_requested_length_counts_unicode_characters(self):
        text='ab😀한글END';ref=text_ref(text)
        page=page_text(text,ref=ref,offset=2,length=2,budget=100,render=lambda s,*_:s)
        self.assertEqual((page.text,page.start,page.end),('😀한',2,4))
        for length in (0,-1,True,1.5):
            with self.assertRaises(ValueError):
                page_text(text,ref=ref,length=length,budget=100,render=lambda s,*_:s)

    def test_context_limit_reports_full_transmitted_prompt_size(self):
        candidate={'label':'x','can':['read'],'line':'0 StaticText Value: '+'v'*20000}
        prompts=[]
        def respond(q,**kwargs):
            prompts.append(q.prompt)
            refs=json.JSONDecoder().raw_decode(q.prompt.split('# 참조 원문\n')[1])[0]
            if len(prompts)==2:
                payload=json.loads(q.prompt.split('# 참조 내용 (관측 데이터)\n')[1])
                self.assertEqual(payload['end']-payload['start'],12)
            for length in (0,-1,True,'12'):
                self.assertIsNone(q.check({'read_context':{'ref':refs[0]['ref'],'offset':0,'length':length}}))
            return q.check({'read_context':{'ref':refs[0]['ref'],'offset':0,'length':12}})
        with patch('tacto.ask.ask',side_effect=respond):
            result=ask_here_paged('read',[candidate],max_pages=2)
        self.assertEqual(result['stop_reason'],'context_page_limit')
        self.assertEqual(result['paging']['token_count'],len(prompts[-1].encode()))

    def test_context_tail_access_keeps_original_action_and_bounded_prompts(self):
        value='v'*40000+' CANDIDATE_END'
        candidate={'label':'long value','can':['read'],'line':'0 StaticText Value: '+value}
        history=['read value: '+'h'*40000+' HISTORY_END']
        stages=[]
        def respond(q,**kwargs):
            self.assertLessEqual(len(q.prompt.encode()),16000)
            refs=json.JSONDecoder().raw_decode(q.prompt.split('# 참조 원문\n')[1])[0]
            stages.append(q.prompt)
            if len(stages)==1:
                r=next(r for r in refs if r['kind']=='complete_candidate')
                return q.check({'read_context':{'ref':r['ref'],'offset':r['characters']-200}})
            if len(stages)==2:
                payload=json.loads(q.prompt.split('# 참조 내용 (관측 데이터)\n')[1])
                self.assertIn('CANDIDATE_END',payload['text'])
                r=next(r for r in refs if r['kind']=='complete_history')
                return q.check({'read_context':{'ref':r['ref'],'offset':r['characters']-200}})
            payload=json.loads(q.prompt.split('# 참조 내용 (관측 데이터)\n')[1])
            self.assertIn('HISTORY_END',payload['text'])
            self.assertIn('CANDIDATE_END',q.prompt)
            self.assertIn('provided_ranges',q.prompt)
            return q.check({'actions':[{'pick':0,'do':'read'}]})
        with patch('tacto.ask.ask',side_effect=respond):
            answer=ask_here_paged('inspect the tails then read',[candidate],history)
        self.assertEqual(len(stages),3)
        self.assertEqual(answer['actions'][0]['candidate'],candidate)

    def test_context_requests_reject_mixed_stale_or_invalid_offsets(self):
        candidate={'label':'x','can':['read'],'line':'0 StaticText Value: '+'v'*20000}
        def respond(q,**kwargs):
            refs=json.JSONDecoder().raw_decode(q.prompt.split('# 참조 원문\n')[1])[0]
            ref=refs[0]['ref']
            for answer in ({'read_context':{'ref':ref,'offset':True}},
                           {'read_context':{'ref':'stale','offset':0}},
                           {'read_context':{'ref':ref,'offset':-1}},
                           {'read_context':{'ref':ref,'offset':0},'done':True}):
                self.assertIsNone(q.check(answer))
            return q.check({'read_context':{'ref':ref,'offset':0}})
        with patch('tacto.ask.ask',side_effect=respond) as ask:
            result=ask_here_paged('read',[candidate],max_pages=1)
        self.assertEqual(result['stop_reason'],'context_page_limit')
        self.assertEqual(ask.call_count,1)

    def test_reference_cannot_cross_invocations_and_candidate_snapshot_is_frozen(self):
        candidate={'label':'x','can':['read'],'line':'0 StaticText Value: '+'v'*20000}
        original=dict(candidate);remembered=[]
        def first(q,**kwargs):
            refs=json.JSONDecoder().raw_decode(q.prompt.split('# 참조 원문\n')[1])[0]
            remembered.append(refs[0]['ref'])
            candidate['line']='0 StaticText Value: changed externally'
            return q.check({'actions':[{'pick':0,'do':'read'}]})
        with patch('tacto.ask.ask',side_effect=first):
            answer=ask_here_paged('read',[candidate],iter(['old history']))
        self.assertEqual(answer['actions'][0]['candidate'],original)
        def second(q,**kwargs):
            self.assertIsNone(q.check({'read_context':{'ref':remembered[0],'offset':0}}))
            return None
        with patch('tacto.ask.ask',side_effect=second):
            ask_here_paged('read',[original],['old history'])
