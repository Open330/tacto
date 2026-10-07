"""General menu commits use direct witnesses and operation-bound disk receipts.

The driver is an in-process fixture; the save and host readback use a real
temporary file. These tests do not claim native AX or application coverage.
"""
import hashlib
from pathlib import Path
import tempfile
import unittest

from nlc.commit_evidence import ArtifactEvidence, CommitEvidence, SavePanelCommitPreparation
from nlc.contract import Envelope
from nlc.general import run
from nlc.outcome import DONE, spec_for, verify


class FileMenuApp:
    capabilities = {"menu_items", "action.menu"}

    def __init__(self, folder, *, fail_probe=False, corrupt=False):
        self.folder = folder
        self.path = Path(folder) / "note.txt"
        self.fail_probe = fail_probe
        self.corrupt = corrupt
        self.writes = 0
        self.probes = []

    def tree(self):
        values = {"location": self.folder, "filename": "note.txt",
                  "overwrite": "", "readback": self.path.read_text() if self.path.exists() else ""}
        return "0 Window Title: Fixture\n" + "\n".join(
            f"  {index} TextField Identifier: {name}, Value: {value}"
            for index, (name, value) in enumerate(values.items(), 1))

    def observe(self, *args, **kwargs):
        return {"type": "observation", "obs_id": "fixture-observation", "tree": self.tree()}

    def menu_items(self, target, path):
        title, submenu = ("File", True) if path == [] else ("Save", False)
        return {"type": "menu_items", "path": path, "complete": True,
                "items": [{"title": title, "path": [*path, title],
                           "enabled": True, "has_submenu": submenu}]}

    def probe(self, obs_id, target, element_index):
        self.probes.append(element_index)
        if self.fail_probe:
            return {"type": "error", "error": "stale_observation"}
        return {"type": "probe", "line": self.tree().splitlines()[element_index]}

    def act(self, obs_id, target, action, **kwargs):
        if action["name"] == "menu":
            if action["path"] != ["File", "Save"]:
                raise AssertionError("unexpected menu command")
            self.path.write_text("corrupt" if self.corrupt else "hello")
            self.writes += 1
        elif action["name"] != "read":
            raise AssertionError("unexpected action")
        return {"type": "acted"}


class GeneralMenuCommitTests(unittest.TestCase):
    def execute(self, app, receipt_mode="valid", *, guarded=False):
        env = Envelope(quote="save note.txt", allows=frozenset({"read", "write_persistent"}), scope="item")
        spec = spec_for("save_file", env,
                        {"저장 위치": app.folder, "저장한 이름": "note.txt", "다시 읽은 내용": "hello"},
                        {"저장 위치": "request", "저장한 이름": "request", "다시 읽은 내용": "prior"})
        stages = iter((("inspect_menu", "메뉴 목록 조회"), ("inspect_menu", "File"),
                       ("menu", "Save"), ("read", "readback")))
        if guarded:
            stages = iter((("click", "Save"), ("read", "readback")))

        def here(goal, candidates, history):
            stage = next(stages, None)
            if stage is None:
                return {"done": True}
            operation, label = stage
            candidate = next(c for c in candidates
                             if c["match" if operation == "read" else "label"] == label)
            return {"actions": [{"do": operation, "candidate": candidate}]}

        def where(name, candidates):
            identifier = {"저장 위치": "location", "저장한 이름": "filename",
                          "요청하지 않은 덮어쓰기 없음": "overwrite", "다시 읽은 내용": "readback"}[name]
            candidate = next(c for c in candidates if c["match"] == identifier)
            return {"pick": candidates.index(candidate), "candidate": candidate}

        def after(event, response, before):
            if guarded: before=before.receipt_context
            if receipt_mode == "missing" or app.writes != before + 1:
                return None
            return CommitEvidence("unrelated-event" if receipt_mode == "wrong_event" else event["event_id"],
                                  event["action_digest"],
                                  ArtifactEvidence(f"write-{app.writes}", str(app.path),
                                                   hashlib.sha256(app.path.read_bytes()).hexdigest()))

        def prepare(event):
            if not guarded: return app.writes
            return SavePanelCommitPreparation(event['event_id'], event['action_digest'],
                event['obs_id'], 'panel', app.writes)

        result = run(app, "fixture", "save note.txt", here, envelope=env, spec=spec,
                     ask_where=where, approve=lambda _: True,
                     prepare_commit=prepare, after_commit=after)
        return result, verify(spec, result, result.after_tree)

    def test_general_uncertain_guarded_save_requires_receipt_before_more_planning(self):
        class GuardedApp(FileMenuApp):
            capabilities={'action.click.save_panel_guard'}
            def tree(self): return super().tree() + '\n  5 Button Title: Save'
            def act(self, obs_id, target, action, **kwargs):
                if action['name']=='click':
                    if kwargs.get('save_panel_token')!='panel' or kwargs.get('approved') is not True:
                        raise AssertionError('missing native guard or approval')
                    self.path.write_text('hello');self.writes+=1
                    return {'type':'acted','unverified':'cannot_complete'}
                return super().act(obs_id,target,action,**kwargs)
        for mode in ('valid','missing','wrong_event'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                app=GuardedApp(folder)
                result,receipt=self.execute(app,mode,guarded=True)
                self.assertEqual(app.writes,1)
                if mode=='valid':
                    self.assertEqual(receipt.state,DONE,(result.why,receipt))
                    self.assertEqual(result.events[0]['state'],'confirmed')
                else:
                    self.assertFalse(result.ok)
                    self.assertEqual(result.events[0]['state'],'unknown')
                    self.assertEqual(result.certainty,'unknown')
                    self.assertEqual(result.commit_evidence,{})

    def test_allowed_save_menu_reaches_done_with_direct_witness_and_disk_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            app = FileMenuApp(folder)
            result, receipt = self.execute(app)
            self.assertEqual(DONE, receipt.state, (result.why, receipt))
            self.assertEqual(1, app.writes)
            self.assertEqual("hello", app.path.read_text())
            self.assertTrue({1, 2, 3, 4} <= set(app.probes))
            self.assertEqual("confirmed", result.certainty)
            self.assertEqual("confirmed", result.events[0]["state"])
            self.assertEqual({"name": "menu", "path": ["File", "Save"]}, result.events[0]["action"])

    def test_missing_unrelated_or_corrupt_receipt_never_confirms_menu_save(self):
        for mode in ("missing", "wrong_event", "corrupt_content"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                app = FileMenuApp(folder, corrupt=mode == "corrupt_content")
                result, receipt = self.execute(app, mode)
                self.assertEqual(1, app.writes)
                self.assertNotEqual(DONE, receipt.state)
                self.assertEqual("unknown", result.certainty)
                self.assertEqual("dispatched", result.events[0]["state"])
                self.assertEqual({}, result.commit_evidence)

    def test_failed_direct_witness_prevents_menu_save_despite_complete_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            app = FileMenuApp(folder, fail_probe=True)
            result, receipt = self.execute(app)
            self.assertNotEqual(DONE, receipt.state)
            self.assertFalse(result.ok)
            self.assertEqual(0, app.writes)
            self.assertFalse(app.path.exists())
            self.assertEqual([], result.events)
