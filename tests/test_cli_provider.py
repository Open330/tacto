"""An explicit backbone must reach every model boundary of nlc do."""
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nlc import cli


class CLIProvider(unittest.TestCase):
    def test_general_limits_are_validated_and_removed_from_task(self):
        limits, words = cli._general_limits_from(['read this','--general-timeout=90',
            '--max-screens','12','--prompt-budget','6000','--max-pages=4'])
        self.assertEqual(['read this'],words)
        self.assertEqual({'timeout_seconds':90,'max_screens':12,'budget':6000,'max_pages':4},limits)
        for args in (['--general-timeout'],['--max-pages','0'],['--max-screens=-1'],
                     ['--prompt-budget=nan'],['--max-pages=2','--max-pages=3']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                cli._general_limits_from(args)

    def test_provider_is_not_part_of_the_goal(self):
        for flags in (['--provider','codex'],['--provider=codex']):
            self.assertEqual(('codex',['read this','--think','2']),
                             cli._provider_from(['read this',*flags,'--think','2']))

    def test_bad_options_fail_before_permission_or_model_access(self):
        for flags in (['--provider'],['--provider='],['--provider','unknown'],
                      ['--provider','codex','--provider','claude']):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2,cli.main(['do','read this',*flags]))

    def test_provider_crosses_interpret_plan_witness_author_and_recovery(self):
        driver = object()
        captured = {}
        class Host:
            def __init__(self, **kwargs):
                captured.update(kwargs)
            def resolve(self, intent):
                return None
            def handle(self, intent, **kwargs):
                captured['intent'] = intent
                captured['interpret'](SimpleNamespace(kind='reading'))
                captured['author'](intent,driver)
                captured['general']('app',intent,lambda _: False)
                return SimpleNamespace(status='done',say='ok',flow=None)
        def run(d,target,goal,here,**kwargs):
            candidates = [{'label':'name','role':'TextField','can':['read']}]
            here(goal,candidates,[])
            kwargs['ask_where']('recipient',candidates)
        modules = {'nlc.onboard':SimpleNamespace(run=lambda **kw:True),
                   'nlc.session':SimpleNamespace(preflight=lambda:{'verdict':'ready'}, sandboxed=lambda:False, SANDBOXED='',
                                                 KeepAwake=contextlib.nullcontext)}
        with patch.dict('sys.modules',modules), patch.object(cli,'Agent',Host), \
             patch.object(cli,'_make_driver',return_value=driver), \
             patch.object(cli,'_indicator_for',return_value=contextlib.nullcontext()), \
             patch('nlc.ask.ask',return_value=None) as ask, \
             patch('nlc.authoring.author') as author, \
             patch('nlc.general.run',side_effect=run), \
             patch('nlc.judge.model_judge',return_value=Mock()) as judge, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0,cli.main(['do','read this','--provider','codex','--learn','--think','1']))
        self.assertEqual('read this',captured['intent'])
        self.assertEqual(3,ask.call_count)
        self.assertTrue(all(c.kwargs['provider']=='codex' for c in ask.call_args_list))
        self.assertEqual('codex',author.call_args.kwargs['provider'])
        self.assertEqual('codex',judge.call_args.kwargs['provider'])


if __name__ == '__main__':
    unittest.main()
