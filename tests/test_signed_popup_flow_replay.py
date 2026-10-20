"""Owned flow replay probe refuses before UI and preserves private evidence."""
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock
from examples.probes.signed_popup_flow_replay import main,reserve_report,owned_flow

class SignedPopupFlowReplayTests(unittest.TestCase):
    def test_existing_or_nonprivate_report_refuses_before_driver(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();output=base/'report.json';output.write_text('KEEP')
            driver=Mock()
            with self.assertRaises(FileExistsError):main(driver,output,42)
            driver.observe.assert_not_called();driver.act.assert_not_called()
            self.assertEqual('KEEP',output.read_text())
            public=base/'public';public.mkdir(mode=0o755)
            with self.assertRaises(ValueError):main(driver,public/'report.json',42)
            driver.observe.assert_not_called();driver.act.assert_not_called()

    def test_invalid_pid_and_missing_capability_are_recorded_without_input(self):
        for pid in (True,0,42):
            with tempfile.TemporaryDirectory() as folder:
                output=Path(folder).resolve()/'report.json';driver=Mock();driver.capabilities=()
                report=main(driver,output,pid)
                self.assertFalse(report['passed']);self.assertTrue(report['source_stable'])
                self.assertEqual('ValueError',report['error_class'])
                driver.observe.assert_not_called();driver.act.assert_not_called()
                self.assertEqual(0o600,stat.S_IMODE(output.stat().st_mode))
                self.assertEqual([],json.loads(output.read_text())['dispatches'])

    def test_serialized_owned_flow_keeps_contracts_and_checked_receiving_fields(self):
        flow=owned_flow()
        self.assertEqual(['open_select','select_custom_option']*2,[s.action for s in flow.steps])
        self.assertEqual(['Beta','Alpha'],[s.params['option_label'] for s in flow.steps[1::2]])
        for step in flow.steps:
            self.assertIsNotNone(step.safety)
            self.assertEqual('tacto-fixture-choice',step.selector.identifier)
        for step in flow.steps[::2]:self.assertEqual({'opening_action':'AXPress'},step.params)
        for step in flow.steps[1::2]:
            self.assertEqual('tacto-fixture-result',step.postcondition.field.identifier)
            self.assertEqual('Value',step.postcondition.attribute)

    def test_wrong_first_or_second_transition_stops_real_replay_before_next_input(self):
        from examples.probes.signed_popup_flow_replay import checked_transition_response
        from tests.test_native_select_loop import Popup
        from tacto.flow import replay
        class Guarded(Popup):
            capabilities=Popup.capabilities|{'action.open_select.explicit_ax'}
            @property
            def tree(self):
                return ('0 Window Title: Native\n'
                        f'  1 PopUpButton Identifier: tacto-fixture-choice, Value: {self.value}\n'
                        f'  2 StaticText Identifier: tacto-fixture-result, Value: {self.value}')
            def act(self,obs_id,target,action,**kwargs):
                response=super().act(obs_id,target,action,**kwargs)
                ordinal=len(self.calls)-1
                count='0' if ordinal==0 else '1' if ordinal<3 else '2'
                if ordinal==self.wrong_ordinal:count='9'
                receiving={'value':self.value,'count':count,'menu_state':'open' if self.opened else 'closed'}
                return checked_transition_response(response,receiving,ordinal,'UNCHANGED_SAMPLED')
        for wrong in (0,1):
            driver=Guarded();driver.wrong_ordinal=wrong
            from tacto.dispatch_journal import DurableDispatchDriver
            with tempfile.TemporaryDirectory() as folder:
                with DurableDispatchDriver(driver,Path(folder).resolve()/'journal') as journalled:
                    result=replay(journalled,owned_flow(),budget=0)
                records=(Path(folder)/'journal'/f'{wrong:05d}.jsonl').read_text().splitlines()
                self.assertEqual('unknown',json.loads(records[-1])['category'])
            self.assertFalse(result.ok,result.log)
            self.assertEqual(wrong+1,len(driver.calls),'later input followed an unexpected transition')
            self.assertEqual('unknown',result.events[-1]['state'])

    def test_bad_audit_or_missing_receiving_field_cannot_accept_action(self):
        from examples.probes.signed_popup_flow_replay import checked_transition_response
        good={'value':'Alpha','count':'0','menu_state':'open'}
        for state,receiving in (('CHANGED',good),('INCONCLUSIVE',good),('UNCHANGED_SAMPLED',{})):
            response=checked_transition_response({'type':'acted'},receiving,0,state)
            self.assertEqual('unknown',response['effect'])
            self.assertTrue(response['reobserve_required'])
