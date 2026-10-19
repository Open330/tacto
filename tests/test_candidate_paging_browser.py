"""Real Chromium candidates/pages and independent DOM receiving counters."""
import importlib.util
import unittest
from urllib.parse import quote
from tacto.candidate_paging import page_candidates,StaleCursor
from tacto.general import candidates

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright required')
class CandidatePagingBrowserTests(unittest.TestCase):
    def setUp(self):
        from tacto.browser import BrowserDriver
        self.driver=BrowserDriver(headless=True);self.addCleanup(self.driver.close)
        buttons=''.join(f'<button data-pick="{i}" onclick="receipt.textContent=\'{i}\';count.textContent=Number(count.textContent)+1">항목 {i:03d}</button>' for i in range(80))
        self.driver.goto('data:text/html;charset=utf-8,'+quote(buttons+'<output id="receipt">none</output><output id="count">0</output>'))
        self.observation=self.driver.observe('page','tree_full')
        self.rows=candidates(self.observation['tree'],capabilities=self.driver.capabilities)

    def test_later_page_uses_original_control_index_and_clicks_once(self):
        cursor=None;seen=[];pages=0;target=None
        while True:
            page=page_candidates(self.rows,budget=1000,cursor=cursor,context=self.observation['obs_id'])
            self.assertFalse(page.overflow);self.assertLessEqual(page.token_count,1000)
            pages+=1;seen.extend(page.picks)
            for pick,row in page.items:
                if row['label']=='항목 079' and 'click' in row['can']:target=(pick,row)
            cursor=page.next_cursor
            if cursor is None:break
        self.assertGreater(pages,1);self.assertEqual(seen,list(range(len(self.rows))))
        self.assertIsNotNone(target);pick,row=target
        self.assertEqual(row['element_index'],self.rows[pick]['element_index'])
        response=self.driver.act(self.observation['obs_id'],'page',{'name':'click','element_index':row['element_index']})
        self.assertEqual(response['type'],'acted',response)
        self.assertEqual(self.driver._page.locator('#receipt').text_content(),'79')
        self.assertEqual(self.driver._page.locator('#count').text_content(),'1')

    def test_mutated_candidate_context_refuses_resume_without_browser_input(self):
        page=page_candidates(self.rows,budget=1,context=self.observation['obs_id'])
        original=list(page.candidates[0]['can'])
        self.rows[0]['can'].append('delete')
        self.assertEqual(page.candidates[0]['can'],original)
        with self.assertRaises(StaleCursor):
            page_candidates(self.rows,budget=1000,cursor=page.next_cursor,context=self.observation['obs_id'])
        self.assertEqual(self.driver._page.locator('#count').text_content(),'0')
