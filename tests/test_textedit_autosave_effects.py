"""Pure dispatch/contract regressions; no native apps or model calls."""
import unittest
from tacto import safety
from tacto.contract import Resource, SafetyContract, ContextBinding, issue_approval
from tacto.flow import Flow, Step, Selector, replay
from tacto.general import run
from tacto.library import textedit_write_note, textedit_emphasize

TARGET='com.apple.TextEdit'
BODY='1 TextArea Identifier: First Text View, Value: hello'

class Driver:
    def __init__(self): self.actions=[]
    def identity(self,target): return target
    def observe(self,*a,**kw):
        return dict(type='observation',obs_id='o',tree='0 Window Title: Untitled [focused]\n  '+BODY)
    def act(self,*args,**kwargs):
        self.actions.append(args[2]); return {'type':'acted'}

class AutosaveEffectsTests(unittest.TestCase):
    def test_body_edits_without_path_are_persistent_and_other_fields_are_not(self):
        for action in ('type_text','set_value'):
            for line in (BODY,'2 AXTextArea Value: unnamed document'):
                self.assertEqual({'write_persistent'},safety.effects_of({'name':action},line,target=TARGET)[0])
            for line in ('2 TextField Identifier: saveAsNameTextField, Value: report.rtf',
                         '2 TextField Identifier: search, Value: hello'):
                self.assertEqual({'write_ephemeral'},safety.effects_of({'name':action},line,target=TARGET)[0])
        self.assertEqual({'write_persistent'},safety.effects_of({'name':'type_text'},target=TARGET)[0])
        self.assertEqual({'write_ephemeral'},safety.effects_of({'name':'set_value'},BODY,target='other.app')[0])
        self.assertEqual({'credential'},safety.effects_of({'name':'set_value'},'1 TextArea Title: password',target=TARGET)[0])

    def test_edit_keys_are_persistent_but_navigation_is_exempt(self):
        from tacto.protocol import NAVIGATION_KEYS
        for key in ('cmd+v','cmd+x','cmd+z','cmd+shift+z','delete','cmd+backspace',
                    'cmd+b','cmd+i','cmd+u','Return','a','shift+A'):
            self.assertEqual({'write_persistent'},safety.effects_of({'name':'press_key','key':key},target=TARGET)[0],key)
        for key in NAVIGATION_KEYS:
            self.assertEqual({'write_ephemeral'},safety.effects_of({'name':'press_key','key':key},target=TARGET)[0])
        self.assertEqual({'write_persistent'},safety.effects_of({'name':'set_value'},target=TARGET)[0])

    def test_document_menu_mutations_but_not_save_panel_or_selection(self):
        for path in (['Format',['Font'],['Bold','볼드']],['Edit','Paste'],['Format','Make Plain Text']):
            self.assertEqual({'write_persistent'},safety.effects_of({'name':'menu','path':path},target=TARGET)[0])
        for path in (['Edit','Select All'],['File','Save As…'],['Format','Font','Show Fonts']):
            self.assertFalse(safety.document_edit_effects({'name':'menu','path':path},target=TARGET))

    def test_ephemeral_body_and_format_contracts_never_dispatch(self):
        for step in (Step('set_value',Selector(identifier='First Text View'),params={'value':'changed'}),
                     Step('menu',params={'path':['Format','Font','Bold']},unverified_ok=True),
                     Step('press_key',params={'key':'cmd+v'},unverified_ok=True)):
            step.safety=SafetyContract(effects=['write_ephemeral'],resource=Resource(scope='entry'),reversibility='reversible')
            d=Driver();result=replay(d,Flow('edit',TARGET,[step]))
            self.assertFalse(result.ok,result.log)
            self.assertIn('contract_effect_mismatch',str(result.log))
            self.assertEqual([],d.actions)

    def test_general_minimal_envelope_cannot_edit_document(self):
        d=Driver()
        def planner(goal,candidates,history):
            body=next(c for c in candidates if c['role']=='TextArea')
            return {'done':False,'actions':[{'do':'set_value','candidate':body,'value':'changed'}],'expect':None}
        result=run(d,TARGET,'edit',planner,max_screens=1)
        self.assertFalse(result.ok)
        self.assertIn("범위를 벗어납니다",result.why)
        self.assertEqual([],d.actions)

    def test_explicit_persistent_contract_dispatches_but_cannot_claim_saved_without_receipt(self):
        contract=SafetyContract(effects=['write_persistent'],resource=Resource(scope='item'),reversibility='recoverable')
        binding=ContextBinding(bundle_id=TARGET,operation='set_value')
        step=Step('set_value',Selector(identifier='First Text View'),params={'value':'changed'},safety=contract,binding=binding)
        d=Driver();result=replay(d,Flow('edit',TARGET,[step]),approvals={0:issue_approval(binding,contract)})
        self.assertEqual(1,len(d.actions),result.log)
        self.assertFalse(result.ok)
        self.assertEqual('unverified',result.log[-1]['status'])

    def test_promote_keeps_document_persistence(self):
        from tacto.promote import Candidate,to_flow
        from tacto.contract import Envelope
        c=Candidate(goal='edit',target=TARGET,envelope=Envelope(
            quote='edit document',allows=frozenset({'write_persistent'}),scope='item',reversibility='recoverable'),
            actions=[dict(do='set_value',role='TextArea',attr='Identifier',match='First Text View',
                          line=BODY,value='changed',label='body')])
        self.assertEqual(['write_persistent'],to_flow(c,'edit').steps[0].safety.effects)

    def test_builtin_document_recipes_do_not_claim_ephemeral_editing(self):
        self.assertEqual(['write_persistent'],textedit_write_note().steps[0].safety.effects)
        self.assertEqual(['write_persistent'],textedit_emphasize().steps[1].safety.effects)
