"""Internal point input never activates, retries, or posts beyond a failed gate."""
from types import SimpleNamespace
import unittest
from nlc.native_window_dispatch import dispatch_point, UserInputState, WindowDispatchFailure


class WindowPointDispatchTests(unittest.TestCase):
    def execute(self, mode='success'):
        posts=[]; reads=[0]; stop=[False]; clock=[10]; checks=[]
        def route(binding, *, element=False):
            checks.append(element)
            if mode=='wrong_window' or (mode=='changed_window' and posts):
                raise RuntimeError('target window changed')
        binding=SimpleNamespace(point_kind='window_point', element='window', window='window',
            pid=42,window_id=17,window_bounds=(10,20,300,200),point=(90,110),
            _backend=SimpleNamespace(equal=lambda a,b:a==b,require_window_target=route))
        def read(scope):
            scope.ready();reads[0]+=1
            changed=(mode=='pointer_before' and reads[0]>=2) or (mode=='pointer_between' and reads[0]>=5)
            focused='other' if mode=='focus_between' and reads[0]>=5 else 'focus'
            return UserInputState(77,(1,2) if changed else (0,0),focused)
        def post(event,pid):
            self.assertEqual(pid,42);posts.append(event)
            if mode=='stop':stop[0]=True
            if mode=='expire':clock[0]=1001
            if mode=='post_error':raise RuntimeError('uncertain post')
        backend=SimpleNamespace(read_input=read,equal=lambda a,b:a==b,post=post,
            activation=lambda *a:self.fail('coordinate pair must not include app activation'))
        try:
            result=dispatch_point(binding,deadline=1000,stopped=lambda:stop[0],context=lambda:None,
                backend=backend,clock=lambda:clock[0],validate=lambda b:None,
                build_pair=lambda *a:SimpleNamespace(down='down',up='up',_owners=('down','up')))
            return posts,result,None
        except WindowDispatchFailure as error:
            return posts,None,error

    def test_point_posts_only_one_pair_without_activation_and_requires_fresh_result(self):
        posts,result,error=self.execute()
        self.assertIsNone(error)
        self.assertEqual(posts,['down','up'])
        self.assertEqual(result['input_route'],'window_bound_mouse')
        self.assertEqual(result['posted_events'],2)
        self.assertEqual(result['unverified'],'observe_effect')
        self.assertIs(result['reobserve_required'],True)

    def test_initial_or_mid_pair_drift_stops_without_cleanup_or_retry(self):
        for mode,attempted in [('wrong_window',0),('pointer_before',0),('changed_window',1),
                               ('pointer_between',1),('focus_between',1),('stop',1),('expire',1),('post_error',1)]:
            with self.subTest(mode=mode):
                posts,result,error=self.execute(mode)
                self.assertIsNone(result)
                self.assertEqual(len(posts),attempted)
                self.assertEqual(error.attempted,attempted)
