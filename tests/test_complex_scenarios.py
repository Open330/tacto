import unittest
from examples.benchmark_complex_scenarios import recipe,job,params,Fixture
from tacto.flow import Flow


class ComplexScenarioTests(unittest.TestCase):
    def test_complex_recipe_binds_derived_result_and_preserves_all_checkpoints(self):
        flow=recipe('owned-fixture');self.assertEqual(11,len(flow.steps))
        for n in range(1,11):
            values=job(n,'case');bound=flow.bind(**params(values))
            self.assertEqual(str(values['result']),bound.steps[6].postcondition.all_of[0].equals)
            self.assertEqual(['review','computed','audited','done'],[bound.steps[i].postcondition.equals for i in (5,6,8,9)])
            self.assertEqual(str(int(values['bonus'])),bound.steps[4].skip_if.equals)
            self.assertEqual('1',bound.steps[9].postcondition.all_of[1].equals)
        self.assertEqual(flow,Flow.from_json(flow.to_json(compact=True)))

    def test_oracle_rejects_wrong_value_or_duplicate_actions_even_when_done(self):
        values=job(1,'case');fixture=Fixture.__new__(Fixture);fixture.sequence=3
        expected={**values,'sequence':3}
        state={'phase':'done','active':False,'commits':[expected],
               'actions':[{'sequence':3,'operation':name} for name in ('review','compute','audit','finish')]}
        fixture.state=lambda:state
        self.assertTrue(fixture.oracle(values)['passed'])
        state['actions'].append({'sequence':3,'operation':'review'})
        self.assertFalse(fixture.oracle(values)['passed'])
        state['actions'].pop();state['commits'][0]={**expected,'result':999}
        self.assertFalse(fixture.oracle(values)['passed'])

    def test_prior_task_history_cannot_satisfy_current_oracle(self):
        values=job(1,'case');fixture=Fixture.__new__(Fixture);fixture.sequence=4
        fixture.state=lambda:{'phase':'done','active':False,'commits':[{**values,'sequence':3}],
                              'actions':[{'sequence':3,'operation':name} for name in ('review','compute','audit','finish')]}
        self.assertFalse(fixture.oracle(values)['passed'])

if __name__=='__main__':unittest.main()
