"""Calculator setup launches only an absent target and verifies the start."""
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock,patch

from tests.test_product_comparison_provenance import comparison


class Witness:
    activations=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def summary(self):return {'initial':'other-app','activations':self.activations}


class CalculatorSetupTests(unittest.TestCase):
    def execute(self,first,*,locked=False,launch_exit=0,activations=(),display='0',mode='basic',expired=False):
        driver=Mock();driver.observe.side_effect=[first]+[{'type':'observation','obs_id':'fresh','tree':'owned'}]*5
        driver.act.return_value={'type':'acted'}
        witness=Witness();witness.activations=list(activations)
        with patch.object(comparison,'_driver',return_value=driver),patch.object(comparison,'_locked',return_value=locked),\
             patch.object(comparison,'_calc_display',return_value=display),patch.object(comparison,'_calc_mode',return_value=mode),\
             patch('tacto.flow.Selector.resolve',return_value=(0,'owned')),patch.object(comparison.time,'sleep'),\
             patch.dict(sys.modules,{'focus_witness':types.SimpleNamespace(FocusWitness=lambda:witness)}),\
             patch.object(comparison.subprocess,'run',return_value=subprocess.CompletedProcess([],launch_exit)) as launch,\
             patch.object(comparison.time,'monotonic',side_effect=[0,9] if expired else None,return_value=0):
            try:return comparison.calc_reset(),None,driver,launch
            except RuntimeError as error:return None,str(error),driver,launch

    def test_absent_target_launches_background_then_verifies_initial_state(self):
        evidence,error,driver,launch=self.execute({'type':'error','error':'target_not_found'})
        self.assertIsNone(error)
        launch.assert_called_once()
        self.assertEqual(['open','-g','-b',comparison.CALC],launch.call_args.args[0])
        self.assertEqual('0',evidence['calculator_initial_display'])
        self.assertTrue(evidence['harness_launched_app_left_running'])

    def test_other_observation_error_never_launches_or_acts(self):
        _,error,driver,launch=self.execute({'type':'error','error':'permission_denied'})
        self.assertEqual('calculator_observation_unavailable',error)
        launch.assert_not_called();driver.act.assert_not_called()

    def test_locked_launch_failure_activation_and_timeout_prevent_input(self):
        for args,expected in [({'locked':True},'calculator_bootstrap_environment_unavailable'),
                              ({'launch_exit':1},'calculator_background_launch_failed'),
                              ({'activations':['com.apple.calculator']},'calculator_bootstrap_activated_app'),
                              ({'expired':True},'calculator_observation_unavailable')]:
            with self.subTest(args=args):
                _,error,driver,launch=self.execute({'type':'error','error':'target_not_found'},**args)
                self.assertEqual(expected,error);driver.act.assert_not_called()

    def test_running_target_is_not_relaunched(self):
        evidence,error,driver,launch=self.execute({'type':'observation','obs_id':'fresh','tree':'owned'})
        self.assertIsNone(error);launch.assert_not_called()
        self.assertEqual('not_needed',evidence['calculator_bootstrap'])

    def test_incorrect_initial_display_or_mode_is_not_accepted(self):
        for args in ({'display':'42'},{'mode':'scientific'}):
            _,error,_,_=self.execute({'type':'observation','obs_id':'fresh','tree':'owned'},**args)
            self.assertEqual('calculator_initial_state_unverified',error)
