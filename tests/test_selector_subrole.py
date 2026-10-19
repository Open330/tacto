import json,unittest
from tacto.flow import Selector,Flow,Step
TREE='0 Window  Title: owned\n  1 Button  Subrole: AXCloseButton\n  2 Button  Subrole: AXMinimizeButton'
class SubroleSelectors(unittest.TestCase):
    def test_close_and_minimize_are_distinct(self):
        self.assertEqual(1,Selector(role='Button',subrole='AXCloseButton',ancestor='owned').resolve(TREE)[0])
        self.assertEqual(2,Selector(role='Button',subrole='AXMinimizeButton',ancestor='owned').resolve(TREE)[0])
    def test_subrole_is_a_fixed_os_identifier(self):
        for value in ('{{x}}','AXCloseButton, Title: other',[],5,'close'):
            with self.subTest(value=value),self.assertRaises(ValueError):Selector(subrole=value)
    def test_binding_and_serialization_preserve_the_match(self):
        f=Flow(name='owned',target='test',steps=[Step('read',selector=Selector(subrole='AXCloseButton',ancestor='owned'))])
        restored=Flow.from_json(f.bind().to_json())
        self.assertEqual(1,restored.steps[0].selector.resolve(TREE)[0])
    def test_unused_subrole_is_not_emitted_to_older_engines(self):
        f=Flow(name='old',target='test',steps=[Step('read',selector=Selector(title='owned'))])
        for compact in (False,True):self.assertNotIn('subrole',json.loads(f.to_json(compact=compact))['steps'][0]['selector'])

class SubroleConsent(unittest.TestCase):
    def test_minimize_approval_cannot_authorize_close(self):
        from tacto.contract import ContextBinding,SafetyContract,Resource,issue_approval,validate
        old=ContextBinding(bundle_id='test',role='Button',subrole='AXMinimizeButton',operation='click')
        new=ContextBinding(bundle_id='test',role='Button',subrole='AXCloseButton',operation='click')
        safety=SafetyContract(effects=['write_ephemeral'],reversibility='reversible',resource=Resource(scope='entry'))
        token=issue_approval(old,safety)
        self.assertFalse(token.authorizes(new,safety)[0])
        self.assertFalse(validate(old,new).ok)
        self.assertFalse(validate(ContextBinding(role='Button'),new).ok)

    def test_old_binding_digest_is_unchanged_when_subrole_is_absent(self):
        from tacto.contract import ContextBinding
        from dataclasses import asdict
        import hashlib
        binding=ContextBinding(bundle_id='test',role='Button')
        old=asdict(binding);old.pop('subrole')
        self.assertEqual(hashlib.sha256(json.dumps(old,sort_keys=True,default=str).encode()).hexdigest()[:32],binding.digest())

    def test_authoring_pins_the_observed_subrole(self):
        from tacto.authoring import _to_flow
        f=_to_flow({'schema':2,'name':'owned','target':'test','steps':[{'action':'click','selector':{'role':'Button','subrole':'AXCloseButton','ancestor':'owned'}}]})
        self.assertEqual('AXCloseButton',f.steps[0].binding.subrole)

class SubroleRepairIdentity(unittest.TestCase):
    def test_offline_repair_cannot_omit_a_pinned_subrole(self):
        from pathlib import Path
        import tempfile
        from tacto.repair_packet import patch_selector
        f=Flow('owned','app',[Step('click',Selector(role='Button',subrole='AXCloseButton',ancestor='owned'))])
        packet={'schema':1,'target':'app','step_index':0,'effect':'not_started','error':'target_missing',
                'action':'click','selector':json.loads(f.to_json())['steps'][0]['selector'],
                'candidates':[{'role':'Button','title':'Close'}]}
        packet['selector'].pop('subrole')
        with tempfile.TemporaryDirectory() as folder:
            source,context,out=[Path(folder)/name for name in ('flow.json','packet.json','out.json')]
            source.write_text(f.to_json());context.write_text(json.dumps(packet))
            with self.assertRaisesRegex(ValueError,'does not match'):
                patch_selector(source,context,{'role':'Button','title':'Close'},out)
            self.assertFalse(out.exists())
