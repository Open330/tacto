"""Plans must fit the actions actually offered by the current driver."""
import unittest

from nlc.ask import NAVIGATION_KEYS, what_to_do_here


class PlannerActions(unittest.TestCase):
    def question(self, can=('scroll', 'press_key')):
        return what_to_do_here('find the next field', [
            {'label':'form', 'role':'ScrollArea', 'can':list(can)}])

    def plan(self, action, can=('scroll', 'press_key')):
        return self.question(can).check({'actions':[{'pick':0, **action}]})

    def test_scroll_preserves_direction_and_bounded_pages(self):
        for direction in ('up','down','left','right'):
            out = self.plan({'do':'scroll','direction':direction,'pages':3})
            self.assertEqual(direction,out['actions'][0]['direction'])
            self.assertEqual(3,out['actions'][0]['pages'])
        out = self.plan({'do':'scroll','direction':'down'})
        self.assertEqual(1,out['actions'][0]['pages'])

    def test_planner_sees_current_field_value_including_empty(self):
        q = what_to_do_here('fill the form',[
            {'label':'name','line':'1 textbox Title: name, Value: Alice','can':['set_value']},
            {'label':'note','line':'2 textbox Title: note, Value: ','can':['set_value']}])
        self.assertIn('현재 값: "Alice"',q.prompt)
        self.assertIn('현재 값: ""',q.prompt)

    def test_bad_scroll_parameters_are_rejected(self):
        for direction,pages in [('diagonal',1),([],1),('down',True),('up',0),('left',11),('right',1.5)]:
            self.assertIsNone(self.plan({'do':'scroll','direction':direction,'pages':pages}))

    def test_navigation_requires_an_offered_capability(self):
        for key in NAVIGATION_KEYS:
            out = self.plan({'do':'press_key','key':key})
            self.assertEqual(key,out['actions'][0]['key'])
        self.assertIsNone(self.plan({'do':'press_key','key':'Tab'},can=('scroll',)))
        q = what_to_do_here('navigate',[{'label':'legacy candidate'}])
        self.assertIsNone(q.check({'actions':[{'pick':0,'do':'press_key','key':'Tab'}]}))

    def test_submission_and_arbitrary_chords_are_not_navigation(self):
        for key in ('Return','Space','Delete','cmd+delete','ctrl+c','super+l','alt+Tab','shift+Escape','Tab+Return',None,[]):
            self.assertIsNone(self.plan({'do':'press_key','key':key}))

    def test_native_no_escape_capability_limits_prompt_and_validator(self):
        from nlc.general import candidates
        cs = candidates('0 TextField Title: name',
                        capabilities={'action.press_key','press_key.no_escape'})
        self.assertNotIn('Escape',cs[0]['navigation_keys'])
        q = what_to_do_here('navigate',cs)
        self.assertIn('허용 키:',q.prompt)
        self.assertIsNone(q.check({'actions':[{'pick':0,'do':'press_key','key':'Escape'}]}))
        self.assertIsNotNone(q.check({'actions':[{'pick':0,'do':'press_key','key':'Tab'}]}))
        from nlc.safety import effects_of
        self.assertEqual({'write_ephemeral'}, effects_of(
            {'name':'press_key','key':'Tab'}, '1 Button Title: Send')[0])

    def test_navigation_ends_the_current_plan(self):
        for navigation in ({'do':'scroll','direction':'down'},{'do':'press_key','key':'Tab'}):
            q = self.question(('click','scroll','press_key'))
            click = {'pick':0,'do':'click'}
            self.assertIsNone(q.check({'actions':[{'pick':0,**navigation},click]}))
            self.assertIsNotNone(q.check({'actions':[click,{'pick':0,**navigation}]}))

    def test_mixed_payloads_and_noninteger_indexes_are_rejected(self):
        self.assertIsNone(self.plan({'do':'scroll','direction':'down','key':'Return'}))
        q = self.question()
        self.assertIsNone(q.check({'actions':[{'pick':True,'do':'scroll','direction':'down'}]}))
        self.assertIsNone(q.check({'done':'false','actions':[]}))
        self.assertIsNone(q.check({'done':True,'actions':[{'pick':0,'do':'scroll','direction':'down'}]}))


if __name__ == '__main__':
    unittest.main()
