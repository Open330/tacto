"""CLI journal flags reach actual replay and survive nested IR dispatch."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tacto import cli
from tacto.dispatch_journal import DurableDispatchDriver
from tacto.library import textedit_save_as
from tests.test_replay_commit_evidence import MemoryStore, TextEditFixture


class CLIDispatchJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / 'journal'
        self.recipe = self.root / 'save.json'
        self.flow = textedit_save_as().bind(filename='proof.rtf')
        self.recipe.write_text(self.flow.to_json())
        self.app = TextEditFixture(write=True)
        self.app.install_evidence()
        self.enterContext(patch('tacto.driver.Driver', return_value=self.app))
        self.enterContext(patch('tacto.agent.FlowStore', return_value=MemoryStore()))
        self.enterContext(patch.object(cli, '_indicator_for', side_effect=lambda *args: contextlib.nullcontext()))
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_run_and_replay_restart_keep_single_save_and_release_journal(self):
        args = [str(self.recipe), '--yes', '--journal-dir', str(self.directory)]
        self.assertEqual(0, cli._dispatch('run', args))
        self.assertEqual(1, len(self.app.writes))
        self.assertEqual(1, cli._dispatch('run', args))
        with patch.object(cli, '_load_recipe', return_value=self.flow):
            self.assertEqual(1, cli._dispatch('replay', ['save', '--yes', '--journal-dir='+str(self.directory)]))
        self.assertEqual(1, len(self.app.writes))
        self.assertIsNone(cli._execution_journal.get())
        # A later invocation with no journal option uses its ordinary driver.
        self.assertIs(self.app, cli._make_driver([]))

    def test_do_flag_is_removed_from_intent_and_scoped_descriptor_is_closed(self):
        from tacto.agent import Agent
        captured = {}
        class Host(Agent):
            def handle(self, intent, **kwargs):
                captured['intent'] = intent
                captured['driver'] = self.driver
                return super().handle(intent, **kwargs)
        with patch.object(cli, 'Agent', Host), \
             patch('tacto.onboard.run', return_value=True), \
             patch('tacto.session.preflight', return_value={'verdict':'ready'}):
            self.assertEqual(0, cli._dispatch('do', ['"proof.rtf" 로 저장해줘', '--strict', '--yes',
                                                    '--journal-dir', str(self.directory)]))
        self.assertEqual('"proof.rtf" 로 저장해줘', captured['intent'])
        self.assertIsInstance(captured['driver'], DurableDispatchDriver)
        self.assertIsNone(captured['driver']._directory_fd)
        self.assertEqual(1, len(self.app.writes))

    def test_do_ir_redirect_inherits_journal_without_flag_in_task(self):
        with patch('tacto.session.sandboxed', return_value=False), \
             patch('tacto.session.KeepAwake', side_effect=contextlib.nullcontext):
            args = [str(self.recipe), '--yes', '--journal-dir', str(self.directory)]
            self.assertEqual(0, cli._dispatch('do', args))
            self.assertEqual(1, cli._dispatch('do', args))
        self.assertEqual(1, len(self.app.writes))

    def test_helper_and_trace_wrappers_keep_receipt_and_restart_barrier(self):
        trace = self.root / 'trace.json'
        args = [str(self.recipe), '--yes', '--helper', '--trace', str(trace),
                '--journal-dir', str(self.directory)]
        with patch('tacto.helper.InheritedHelperDriver', return_value=self.app) as helper:
            self.assertEqual(0, cli._dispatch('run', args))
            self.assertEqual(1, cli._dispatch('run', args))
        self.assertEqual(2, helper.call_count)
        self.assertTrue(trace.is_file())
        self.assertEqual(1, len(self.app.writes))

    def test_invalid_option_never_constructs_driver_or_requests_permissions(self):
        for cmd, args in [('run', ['x','--journal-dir']), ('do', ['task','--journal-dir=']),
                          ('run', ['x','--journal-dir','relative']),
                          ('replay', ['x','--journal-dir=/a','--journal-dir=/b']),
                          ('observe', ['app','--journal-dir=/a'])]:
            with self.subTest(cmd=cmd,args=args), patch.object(cli, '_make_driver') as factory, \
                 patch('tacto.onboard.run') as onboard:
                self.assertEqual(2, cli._dispatch(cmd,args))
                factory.assert_not_called()
                onboard.assert_not_called()

    def test_bad_directory_and_dispatch_exception_do_not_leave_live_scope(self):
        self.directory.mkdir(mode=0o755)
        self.assertEqual(2, cli._dispatch('run', [str(self.recipe),'--yes','--journal-dir',str(self.directory)]))
        self.assertEqual([], self.app.writes)
        self.assertIsNone(cli._execution_journal.get())
        self.directory.chmod(0o700)
        captured = {}
        def explode(cmd,args):
            captured['driver'] = cli._make_driver([])
            raise RuntimeError('owned failure')
        with patch.object(cli, '_dispatch_command', side_effect=explode):
            with self.assertRaises(RuntimeError):
                cli._dispatch('run', ['x','--journal-dir',str(self.directory)])
        self.assertIsNone(captured['driver']._directory_fd)
        self.assertIsNone(cli._execution_journal.get())
