"""Already-correct disk bytes do not resolve a lost Save reply."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tacto.flow import Flow,Step,Selector,Postcondition,replay
from tacto.contract import Envelope,ContextBinding,issue_approval
from tacto.commit_evidence import SavedFileStateEvidence,evidence_error
from tacto.existing_file_receipt import ExistingTextFileReceipt,SAVE_PATH
from tests.test_existing_file_receipt import Doc


class SaveDriver(Doc):
    def __init__(self,path,text,*,lost=False):super().__init__(path,text);self.lost=lost;self.calls=0
    def observe(self,*args,**kwargs):
        return {'type':'observation','obs_id':'o','tree':'0 Window Title: doc.txt [focused]'}
    def act(self,*args,**kwargs):
        self.calls+=1
        if self.lost:raise OSError('PRIVATE_SAVE_REPLY')
        return {'type':'acted',**({'unverified':self.unverified} if getattr(self,'unverified',None) else {})}
    def wait_until(self,target,predicate,**kwargs):return self.observe()


class SavedStateTests(unittest.TestCase):
    def fixture(self,lost):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        path=str(Path(tmp.name).resolve()/'doc.txt');Path(path).write_text('already saved')
        driver=SaveDriver(path,'already saved',lost=lost)
        receipt=ExistingTextFileReceipt(driver,document_path=path)
        params={'path':SAVE_PATH,'focused_document':path}
        step=Step('menu',params=params,safety=Envelope.minimal().contract_for({'write_persistent'},'document'),
            binding=ContextBinding(bundle_id='com.apple.TextEdit',operation='menu'),
            postcondition=Postcondition(contains='Window'))
        return driver,receipt,Flow('save','com.apple.TextEdit',[step],schema=2)

    def test_state_only_keeps_acknowledged_operation_distinct(self):
        driver,receipt,flow=self.fixture(False)
        result=replay(driver,flow,{0:issue_approval(flow.steps[0].binding,flow.steps[0].safety)},
            prepare_commit=receipt.prepare_commit,after_commit=receipt.after_commit,commit_expected=receipt.commit_expected)
        self.assertTrue(result.ok,result.log)
        self.assertEqual(result.events[0]['state'],'state_verified')
        self.assertEqual(result.events[0]['commit_evidence_kind'],'saved_file_state')
        proof=next(iter(result.commit_evidence.values()))
        self.assertIs(type(proof.evidence),SavedFileStateEvidence)
        self.assertEqual(evidence_error({**result.events[0],'commit_evidence':proof},{}),'saved_state_expectation_missing')

    def test_lost_dispatch_reply_is_not_promoted_and_never_retried(self):
        driver,receipt,flow=self.fixture(True)
        asked=[]
        result=replay(driver,flow,{0:issue_approval(flow.steps[0].binding,flow.steps[0].safety)},
            prepare_commit=receipt.prepare_commit,after_commit=receipt.after_commit,commit_expected=receipt.commit_expected,
            budget=3,escalate=lambda why:asked.append(why))
        self.assertFalse(result.ok)
        self.assertEqual(driver.calls,1);self.assertEqual(asked,[])
        self.assertEqual(result.events[0]['state'],'unknown')
        self.assertTrue(result.events[0]['saved_state_verified'])
        self.assertEqual(len(result.commit_evidence),1)
        self.assertEqual(result.log[-1]['detail'],'saved_state_does_not_confirm_dispatch')

    def test_cli_does_not_say_an_unsaved_file_when_state_is_verified(self):
        from tacto.cli import _run_flow
        driver,receipt,flow=self.fixture(True)
        with patch('tacto.cli._make_driver',return_value=driver), \
                patch('tacto.agent.Agent.commit_adapter_for',return_value=receipt), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code=_run_flow(flow,['--yes','--no-indicator'])
        self.assertEqual(code,1)
        self.assertIn('저장 요청의 실행 여부',output.getvalue())
        self.assertNotIn('저장된 파일이 없어',output.getvalue())

    def test_uncertain_acted_save_preserves_state_without_promoting_dispatch(self):
        for uncertainty in ('cannot_complete','observe_effect'):
            with self.subTest(uncertainty=uncertainty):
                driver,receipt,flow=self.fixture(False);driver.unverified=uncertainty
                result=replay(driver,flow,{0:issue_approval(flow.steps[0].binding,flow.steps[0].safety)},
                    prepare_commit=receipt.prepare_commit,after_commit=receipt.after_commit,
                    commit_expected=receipt.commit_expected,budget=2)
                self.assertFalse(result.ok)
                self.assertEqual(driver.calls,1)
                self.assertEqual(result.events[0]['state'],'unknown')
                self.assertTrue(result.events[0]['saved_state_verified'])
                self.assertEqual(len(result.commit_evidence),1)
                self.assertEqual(result.log[-1]['detail'],'saved_state_does_not_confirm_dispatch')

    def test_general_save_requires_operation_completion_even_with_acknowledged_state(self):
        from tests import test_general_menu_commit as fixture
        from tacto.outcome import DONE
        spec_for=fixture.spec_for
        def state_spec(kind,envelope,expected,sources):
            return spec_for(kind,envelope,{**expected,'artifact_evidence_scope':'saved_state'},
                            {**sources,'artifact_evidence_scope':'request'})
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(fixture,'ArtifactEvidence',SavedFileStateEvidence), \
                patch.object(fixture,'spec_for',side_effect=state_spec):
            app=fixture.FileMenuApp(folder)
            result,verdict=fixture.GeneralMenuCommitTests().execute(app)
        self.assertEqual(app.writes,1)
        self.assertNotEqual(verdict.state,DONE)
        self.assertEqual(result.certainty,'unknown')
        self.assertEqual(result.events[0]['state'],'state_verified')
        self.assertTrue(result.events[0]['saved_state_verified'])
        self.assertEqual(len(result.commit_evidence),1)

    def test_general_uncertain_acted_save_is_unknown_with_state_receipt(self):
        from tests import test_general_menu_commit as fixture
        from tacto.outcome import DONE
        spec_for=fixture.spec_for
        def state_spec(kind,envelope,expected,sources):
            return spec_for(kind,envelope,{**expected,'artifact_evidence_scope':'saved_state'},
                            {**sources,'artifact_evidence_scope':'request'})
        class UncertainApp(fixture.FileMenuApp):
            def act(self,*args,**kwargs):
                result=super().act(*args,**kwargs)
                return {**result,'unverified':'cannot_complete'}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(fixture,'ArtifactEvidence',SavedFileStateEvidence), \
                patch.object(fixture,'spec_for',side_effect=state_spec):
            app=UncertainApp(folder)
            result,verdict=fixture.GeneralMenuCommitTests().execute(app)
        self.assertEqual(app.writes,1)
        self.assertNotEqual(verdict.state,DONE)
        self.assertEqual(result.certainty,'unknown')
        self.assertEqual(result.events[0]['state'],'unknown')
        self.assertEqual(len(result.commit_evidence),1)

    def test_missing_receipt_does_not_infer_a_missing_file_or_recommend_another_save(self):
        from tacto.cli import _run_flow
        driver,_,flow=self.fixture(False)
        with patch('tacto.cli._make_driver',return_value=driver), \
                patch('tacto.agent.Agent.commit_adapter_for',return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code=_run_flow(flow,['--yes','--no-indicator'])
        self.assertEqual(code,1)
        self.assertEqual(driver.calls,1)
        self.assertIn('호스트 영수증이 없습니다',output.getvalue())
        self.assertIn('다시 실행하지 말고',output.getvalue())
        self.assertNotIn('저장된 파일이 없어',output.getvalue())
        self.assertNotIn('nlc replay textedit_save',output.getvalue())
        self.assertNotIn('저장하지 않은 문서라면 정상',output.getvalue())

    def test_persistent_text_edit_reports_uncertain_change_instead_of_save_failure(self):
        from tacto.cli import _run_flow
        driver,_,_=self.fixture(False)
        driver.observe=lambda *a,**k:{'type':'observation','obs_id':'edit',
            'tree':'0 TextArea  Value: before, Identifier: First Text View'}
        step=Step('set_value',selector=Selector(role='TextArea',identifier='First Text View'),
            params={'value':'after'},
            safety=Envelope.minimal().contract_for({'write_persistent'},'document'),
            binding=ContextBinding(bundle_id='com.apple.TextEdit',operation='set_value'))
        flow=Flow('persistent_edit','com.apple.TextEdit',[step],schema=2)
        with patch('tacto.cli._make_driver',return_value=driver), \
                patch('tacto.agent.Agent.commit_adapter_for',return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code=_run_flow(flow,['--yes','--no-indicator'])
        self.assertEqual(code,1)
        self.assertEqual(driver.calls,1)
        self.assertIn('missing_typed_host_commit_evidence',output.getvalue())
        self.assertIn('실행 효과는 미확정',output.getvalue())
        self.assertNotIn('저장된 파일이 없어',output.getvalue())
        self.assertNotIn('textedit_save',output.getvalue())


if __name__=='__main__':unittest.main()
