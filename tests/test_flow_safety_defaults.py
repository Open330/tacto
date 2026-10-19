"""Compact authoring preserves expanded replay behavior and refusal boundaries."""
import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch

from tacto.command_help import calculator_example
from tacto.flow import Flow, replay
from tests.test_runtime_contract import App


class Calculator(App):
    def __init__(self):
        super().__init__()
        self.value = '0'
        self.left = None
        self.names = ['AllClear', 'One', 'Two', 'Multiply', 'Three', 'Four', 'Equals']

    @property
    def tree(self):
        names = list(self.names)
        if self.value != '0':
            names[0] = 'Clear'
        shown = self.value if self.left is None else f'{self.left} × {self.value}'
        return ('0 Window  Title: Calculator\n' + ''.join(
            f'  {i} Button  Identifier: {name}, Title: {name}\n'
            for i, name in enumerate(names, 1)) +
            f'  8 ScrollArea  Identifier: StandardInputView, Value: {shown}')

    def act(self, obs_id, target, action, **kwargs):
        self.calls.append(dict(action))
        name = self.names[action['element_index'] - 1]
        if action['element_index'] == 1 and self.value != '0':
            name = 'Clear'
        if name == 'AllClear':
            self.value, self.left = '0', None
        elif name == 'Clear':
            self.value = '0'
        elif name == 'Multiply':
            self.left, self.value = int(self.value), '0'
        elif name == 'Equals':
            self.value = str(self.left * int(self.value))
            self.left = None
        else:
            digit = {'One': '1', 'Two': '2', 'Three': '3', 'Four': '4'}[name]
            self.value = (self.value if self.value != '0' else '') + digit
        return {'type': 'acted'}


class SafetyDefaultsTests(unittest.TestCase):
    def test_compact_and_expanded_replay_have_same_effect_and_result(self):
        compact = Flow.from_json(json.dumps(calculator_example()))
        expanded = Flow.from_json(compact.to_json())
        self.assertEqual(compact, expanded)
        outcomes = []
        for flow in (compact, expanded):
            app = Calculator()
            result = replay(app, flow)
            self.assertTrue(result.ok, result)
            self.assertEqual('408', app.value)
            self.assertEqual(6, len(app.calls))
            outcomes.append((result.log, app.calls))
        self.assertEqual(*outcomes)

    def test_partial_expression_is_fully_cleared_before_first_digit(self):
        app = Calculator()
        app.left, app.value = 12, '1'
        result = replay(app, Flow.from_json(json.dumps(calculator_example())))
        self.assertTrue(result.ok, result.log)
        self.assertEqual('408', app.value)
        self.assertEqual([1, 1], [call['element_index'] for call in app.calls[:2]])
        self.assertEqual(8, len(app.calls))

    def test_override_changes_only_declared_fields_and_copies_are_independent(self):
        flow = Flow.from_json(json.dumps(calculator_example()))
        self.assertEqual(['delete'], flow.steps[0].safety.effects)
        self.assertEqual('entry', flow.steps[0].safety.resource.scope)
        self.assertEqual(['write_ephemeral'], flow.steps[2].safety.effects)
        flow.steps[0].safety.resource.scope = 'all'
        self.assertEqual('entry', flow.steps[2].safety.resource.scope)

    def test_resource_override_does_not_drop_other_shared_fields(self):
        doc = calculator_example()
        doc['steps'][0]['safety']['resource'] = {'scope': 'item'}
        contract = Flow.from_json(json.dumps(doc)).steps[0].safety
        self.assertEqual('calculator entry', contract.resource.object_type)
        self.assertEqual('item', contract.resource.scope)

    def test_explicit_null_is_refused_before_input(self):
        doc = calculator_example()
        doc['steps'][1]['safety'] = None
        app = Calculator()
        result = replay(app, Flow.from_json(json.dumps(doc)))
        self.assertFalse(result.ok)
        self.assertEqual('contract_required', result.log[-1]['error'])
        self.assertEqual([], app.calls)

    def test_inherited_ephemeral_contract_cannot_hide_send_effect(self):
        doc = calculator_example()
        doc.update(target='mail.fixture', steps=[{'action': 'click', 'selector': {'role': 'Button', 'title': 'Send'}}])
        app = App()
        result = replay(app, Flow.from_json(json.dumps(doc)))
        self.assertFalse(result.ok)
        self.assertEqual('contract_effect_mismatch', result.log[-1]['detail']['error'])
        self.assertEqual([], app.calls)

    def test_no_defaults_preserves_missing_contract_refusal(self):
        doc = calculator_example()
        del doc['safety_defaults']
        result = replay(Calculator(), Flow.from_json(json.dumps(doc)))
        self.assertEqual('contract_required', result.log[-1]['error'])

    def test_malformed_defaults_and_shared_approval_are_load_errors(self):
        for value in (None, [], {'evidence': []}, {'commit_point': True},
                      {'effects': 'read'}, {'resource': None}, {'destination': []},
                      {'data_classes': [1]}, {'reversibility': False}):
            with self.subTest(value=value):
                doc = calculator_example()
                doc['safety_defaults'] = value
                with self.assertRaisesRegex(ValueError, 'Flow.safety_defaults'):
                    Flow.from_json(json.dumps(doc))

    def test_invalid_step_contract_does_not_fall_back_to_defaults(self):
        for value in ('read', False, []):
            doc = calculator_example()
            doc['steps'][0]['safety'] = value
            with self.assertRaisesRegex(ValueError, 'step.safety'):
                Flow.from_json(json.dumps(doc))

    def test_branch_inherits_but_decide_itself_has_no_contract(self):
        doc = calculator_example()
        step = copy.deepcopy(doc['steps'][2])
        doc['steps'] = [{'action': 'decide', 'params': {
            'into': 'choice', 'question': 'Which?', 'choices': ['yes', 'no'],
            'goal': 'choose a digit', 'branches': {'yes': [step]}}}]
        flow = Flow.from_json(json.dumps(doc))
        self.assertIsNone(flow.steps[0].safety)
        self.assertEqual(['write_ephemeral'], flow.steps[0].params['branches']['yes'][0].safety.effects)


class CommandHelpTests(unittest.TestCase):
    def test_help_is_available_without_native_driver_or_sandbox_access(self):
        from tacto.cli import main, _dispatch
        for entry in (main, lambda argv: _dispatch(argv[0], argv[1:])):
            for command in ('observe', 'run'):
                with self.subTest(entry=entry, command=command), \
                     patch('tacto.cli._make_driver', side_effect=AssertionError('native input')), \
                     patch('tacto.session.sandboxed', return_value=True), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(0, entry([command, '--help', *(['--example'] if command == 'run' else [])]))
                    if command == 'run':
                        text = output.getvalue()
                        example = json.loads(text[text.index('{'):])
                        self.assertEqual(8, len(Flow.from_json(json.dumps(example)).steps))
                    else:
                        self.assertIn('com.apple.calculator', output.getvalue())

    def test_short_help_and_explicit_examples_do_not_start_native_runtime(self):
        from tacto.cli import main
        for command in ('run', 'session'):
            for example in (False, True):
                with self.subTest(command=command, example=example), \
                     patch('tacto.cli._make_driver', side_effect=AssertionError('native input')), \
                     patch('tacto.session.sandboxed', return_value=True), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    args = [command, '--help'] + (['--example'] if example else [])
                    self.assertEqual(0, main(args))
                    text = output.getvalue()
                    if example:
                        flow = Flow.from_json(text[text.index('{'):])
                        self.assertEqual('com.apple.calculator', flow.target)
                        self.assertEqual(8, len(flow.steps))
                    else:
                        self.assertNotIn('{', text)
                        self.assertIn('--example', text)
