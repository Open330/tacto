"""Real known-recipe/CLI wiring with a contract fake and temporary RTF files."""
from contextlib import nullcontext, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nlc.agent import Agent
from nlc.library import textedit_save_as
from nlc.trace import Trace, TraceDriver, TracingDriver
from test_replay_commit_evidence import TextEditFixture, MemoryStore


class SnapshotTextEdit(TextEditFixture):
    capabilities = {'evidence.document_snapshot'}
    def __init__(self, directory):
        super().__init__()
        self.directory=directory
        self.document_path=None
        self.snapshot_calls=[]
        self.body='hello'
        self.change_body_on_menu=False
    def document_snapshot(self, target, document_token=None):
        self.snapshot_calls.append((document_token,self.sheet,self.clicks))
        return dict(type='document_snapshot',document_token='document-1',
                    text=self.body,text_complete=True,document_path=self.document_path,
                    source_format='rtf', save_panel=(dict(document_token='document-1',
                    directory=str(self.directory), filename=self.name, format='rtf',
                    commit_control={'obs_id': 'obs', 'enabled': True,
                                    'action': {'name': 'click', 'element_index': 5}})
                    if self.sheet else None))
    def act(self, obs_id, target, action, **kwargs):
        out=super().act(obs_id,target,action,**kwargs)
        if action['name']=='menu' and self.change_body_on_menu:self.body='changed'
        if action['name']=='click':
            path=self.directory/self.name
            path.write_bytes(b'{\\rtf1\\ansi hello}')
            self.document_path=str(path)
        return out


class AgentTextEditReceiptTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.home=Path(temp.name).resolve()
        self.directory=self.home/'Downloads';self.directory.mkdir(mode=0o700)
        self.d=SnapshotTextEdit(self.directory)
        self.patch=patch('pathlib.Path.home',return_value=self.home)
        self.patch.start();self.addCleanup(self.patch.stop)
    def test_agent_known_save_creates_receipt_before_ui_and_preserves_plain_text(self):
        turn=Agent(driver=self.d,store=MemoryStore()).handle('"보고서.rtf" 로 저장해줘',ask=lambda _:True)
        self.assertEqual('done',turn.status,turn)
        self.assertEqual(1,self.d.clicks)
        self.assertEqual((None,False,0),self.d.snapshot_calls[0])
        self.assertTrue((self.directory/'보고서.rtf').exists())
    def test_existing_file_or_document_change_stops_before_save_click(self):
        path=self.directory/'보고서.rtf';path.write_bytes(b'original')
        agent=Agent(driver=self.d,store=MemoryStore())
        turn=agent.handle('"보고서.rtf" 로 저장해줘',ask=lambda _:True)
        self.assertEqual('failed',turn.status)
        self.assertEqual(0,self.d.clicks)
        self.assertFalse(self.d.sheet)
        self.assertEqual(b'original',path.read_bytes())
        path.unlink();self.d.change_body_on_menu=True
        turn=agent.handle('"보고서.rtf" 로 저장해줘',ask=lambda _:True)
        self.assertNotEqual('done',turn.status)
        self.assertEqual(0,self.d.clicks)
        self.assertFalse(path.exists())
    def test_matching_name_with_changed_recipe_cannot_get_builtin_receipt(self):
        f=textedit_save_as().bind(filename='보고서.rtf',folder='Downloads')
        f.steps[0].intent_text='changed recipe'
        self.assertIsNone(Agent(driver=self.d).commit_adapter_for(f))
        self.assertEqual([],self.d.snapshot_calls)
    def test_cli_replay_and_live_trace_use_receipt_but_recording_cannot_issue_one(self):
        from nlc.cli import _run_flow
        d=TracingDriver(self.d,Trace())
        f=textedit_save_as().bind(filename='보고서.rtf',folder='Downloads')
        with patch('nlc.cli._make_driver',return_value=d), \
             patch('nlc.cli._indicator_for',return_value=nullcontext()), \
             patch('nlc.cli._confirmer',return_value=lambda _:True),redirect_stdout(io.StringIO()):
            self.assertEqual(0,_run_flow(f,[]))
        self.assertEqual(1,self.d.clicks)
        self.assertTrue(self.d.snapshot_calls)
        self.assertNotIn('evidence.document_snapshot',TraceDriver(d.trace).capabilities)
        self.assertFalse(any(e.get('kind')=='document_snapshot' for e in d.trace.events))
