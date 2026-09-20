import unittest
from unittest.mock import patch
from nlc.general import run
from nlc.menu_navigation import MenuNavigation

class App:
    capabilities={'menu_items','action.menu'}
    def __init__(self):self.count=0;self.calls=[];self.disabled=False;self.reads=[];self.command="Increment"
    def tree(self):return f'0 Window Title: Fixture\n  1 StaticText Value: {self.count}, Identifier: count'
    def observe(self,*a,**k):return {'type':'observation','obs_id':'obs','tree':self.tree()}
    def menu_items(self,target,path):
        self.reads.append(list(path))
        items=([{'title':'Probe','path':['Probe'],'enabled':True,'has_submenu':True}] if not path else
               [{'title':self.command,'path':['Probe',self.command],'enabled':not self.disabled,'has_submenu':False}])
        return {'type':'menu_items','path':path,'items':items,'complete':True}
    def probe(self,obs,target,index):return {'type':'probe','line':self.tree().splitlines()[index]}
    def act(self,obs,target,action,**kwargs):
        self.calls.append((action,kwargs))
        if action['name']=='menu':self.count+=1
        return {'type':'acted'}

class GeneralMenuTests(unittest.TestCase):
    def planner(self,stages,changed=None):
        count=[0]
        def plan(goal,candidates,history):
            count[0]+=1
            if count[0]>len(stages):return {'done':True}
            action,label=stages[count[0]-1]
            chosen=next(c for c in candidates if c['label']==label and action in c['can'])
            if changed and action=='menu':changed()
            return {'actions':[{'do':action,'candidate':chosen}]}
        return plan
    def test_browse_execute_reobserve_read(self):
        app=App()
        result=run(app,'fixture','increment and read',self.planner([
            ('inspect_menu','메뉴 목록 조회'),('inspect_menu','Probe'),('menu','Increment'),('read','1')]))
        self.assertTrue(result.ok,result.why)
        self.assertEqual(result.said,['1'])
        self.assertEqual(result.certainty,'unknown')
        self.assertEqual(result.events[0]['state'],'dispatched')
        self.assertEqual([a['name'] for a,k in app.calls],['menu','read'])
        self.assertEqual(app.calls[0][0],{'name':'menu','path':['Probe','Increment']})
        self.assertEqual(app.calls[0][1]['expect'],{'no_modal':True})
        from nlc.promote import candidate_from
        from nlc.promote import deployable
        candidate=candidate_from(result,goal='increment',target='fixture',quote='increment')
        self.assertIsNotNone(candidate)
        self.assertIsNone(deployable(candidate,'increment','increment')[0])
    def test_repeat_menu_after_changed_observation_and_stop_unchanged(self):
        for changes in (True,False):
            app=App()
            if not changes:
                def no_change(obs,target,action,**kwargs):
                    app.calls.append((action,kwargs));return {'type':'acted'}
                app.act=no_change
            result=run(app,'fixture','increment twice then read',self.planner([
                ('inspect_menu','메뉴 목록 조회'),('inspect_menu','Probe'),
                ('menu','Increment'),('menu','Increment'),('read','2')]))
            self.assertEqual(result.ok,changes,result.why)
            commands=[a for a,k in app.calls if a['name']=='menu']
            self.assertEqual(len(commands),2 if changes else 1)

    def test_changed_menu_never_dispatches(self):
        app=App()
        result=run(app,'fixture','increment',self.planner([
            ('inspect_menu','메뉴 목록 조회'),('inspect_menu','Probe'),('menu','Increment')],lambda:setattr(app,'disabled',True)))
        self.assertFalse(result.ok)
        self.assertEqual(app.calls,[])
    def test_forged_or_mixed_navigation_never_calls_driver(self):
        for mixed in (False,True):
            app=App()
            def plan(goal,candidates,history):
                c=next(c for c in candidates if 'inspect_menu' in c['can'])
                forged={**c,'menu_path':['Unobserved']}
                actions=[{'do':'inspect_menu','candidate':c if mixed else forged}]
                if mixed:actions.append({'do':'menu','candidate':c})
                return {'actions':actions}
            result=run(app,'fixture','inspect',plan)
            self.assertFalse(result.ok)
            self.assertEqual(app.reads,[])
            self.assertEqual(app.calls,[])
    def test_navigation_budget_cannot_become_success(self):
        app=App()
        def plan(goal,candidates,history):return {'actions':[{'do':'inspect_menu','candidate':next(c for c in candidates if c['label']=='메뉴 목록 조회')}]}
        result=run(app,'fixture','inspect',plan,max_screens=2)
        self.assertFalse(result.ok)
        self.assertEqual(app.calls,[])
    def test_consequential_menu_cannot_bypass_scope_or_evidence(self):
        for command in ('Save','Send','Delete','Cut','Paste','Undo','Bold'):
            app=App();app.command=command
            result=run(app,'com.apple.Notes','inspect menus',self.planner([
                ('inspect_menu','메뉴 목록 조회'),('inspect_menu','Probe'),('menu',command)]))
            self.assertFalse(result.ok)
            self.assertEqual(app.calls,[])

    def test_model_schema_rejects_invented_paths_and_mixed_actions(self):
        from nlc.ask import _here_question
        app=App();nav=MenuNavigation(app,'fixture',app.capabilities)
        c=nav.candidates()[0]
        q=_here_question('inspect',[c],[])
        self.assertIsNotNone(q.check({'actions':[{'pick':0,'do':'inspect_menu'}]}))
        self.assertIsNone(q.check({'actions':[{'pick':0,'do':'inspect_menu','path':['Fake']}]}))
        self.assertIsNone(q.check({'actions':[{'pick':0,'do':'inspect_menu'}]*2}))

    def test_missing_menu_state_fields_are_reported_not_uncaught(self):
        app=App()
        original=app.menu_items
        def malformed(target,path):
            reply=original(target,path)
            reply['items'][0].pop('has_submenu')
            return reply
        app.menu_items=malformed
        result=run(app,'fixture','inspect',self.planner([('inspect_menu','메뉴 목록 조회')]))
        self.assertFalse(result.ok)
        self.assertEqual(app.calls,[])
        self.assertIn('malformed menu item',result.why)

    def test_unknown_state_is_not_executable(self):
        app=App();nav=MenuNavigation(app,'fixture',app.capabilities)
        nav.path=['Probe'];nav.items=[{'title':'Mystery','path':['Probe','Mystery'],'enabled':None,'has_submenu':None}]
        self.assertEqual(nav.candidates()[-1]['can'],['inspect_menu'])
