"""Real Chromium execution-loop checks on local pages and temporary files."""
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

try:
    import playwright.sync_api
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "optional playwright dependency not installed")
class GeneralBrowser(unittest.TestCase):
    def test_save_has_real_disk_readback_and_rejects_wrong_destination(self):
        from nlc.browser import BrowserDriver
        from nlc.contract import Envelope
        from nlc.general import run
        from nlc.outcome import spec_for, verify
        for wrong_path, corrupt in ((False, False), (True, False), (False, True)):
            with self.subTest(wrong_path=wrong_path, corrupt=corrupt), tempfile.TemporaryDirectory() as folder:
                driver = BrowserDriver()
                try:
                    writes = []
                    def prepare_commit(event):
                        return len(writes)
                    def after_commit(event, response, before):
                        from nlc.commit_evidence import CommitEvidence, ArtifactEvidence
                        import hashlib
                        if len(writes) == before + 1:
                            path = writes[-1]
                            return CommitEvidence(event['event_id'], event['action_digest'],
                                ArtifactEvidence(f'write-{len(writes)}',str(path),
                                                 hashlib.sha256(path.read_bytes()).hexdigest()))
                    def save(location, name, content):
                        path = Path(location) / name
                        path.write_text("corrupt" if corrupt else content)
                        writes.append(path)
                        return path.read_text()
                    driver._page.expose_function("saveLocal", save)
                    driver.goto("data:text/html," + quote('''
                        <input aria-label="location"><input aria-label="filename" value="note.txt">
                        <input aria-label="overwrite" value=""><input aria-label="readback" value="">
                        <button onclick="saveLocal(document.querySelector('[aria-label=location]').value,
                          document.querySelector('[aria-label=filename]').value, 'hello').then(value => {
                          document.querySelector('[aria-label=readback]').value = value;
                          document.querySelector('output').textContent = 'Saved';})">Save</button>
                        <output>Draft</output>'''))
                    driver._page.locator('[aria-label=location]').fill(folder + ("/wrong" if wrong_path else ""))
                    env = Envelope(quote="save note.txt", allows=frozenset({"read", "write_persistent"}), scope="item")
                    spec = spec_for("save_file", env,
                        {"저장 위치": folder, "저장한 이름": "note.txt", "다시 읽은 내용": "hello"},
                        {"저장 위치": "request", "저장한 이름": "request", "다시 읽은 내용": "prior"})
                    def here(goal, cands, done):
                        if done:
                            return {"done": True}
                        return {"actions": [{"do": "click", "candidate": next(c for c in cands if c["label"] == "Save")}], "expect": "Saved"}
                    def where(name, cands):
                        label = {"저장 위치": "location", "저장한 이름": "filename",
                                 "요청하지 않은 덮어쓰기 없음": "overwrite", "다시 읽은 내용": "readback"}[name]
                        c = next(c for c in cands if c["label"] == label)
                        return {"pick": cands.index(c), "candidate": c}
                    result = run(driver, "page", "save", here, envelope=env, spec=spec, ask_where=where, approve=lambda _: True,
                                 prepare_commit=prepare_commit, after_commit=after_commit)
                    receipt = verify(spec, result, result.after_tree)
                    self.assertEqual(not wrong_path and not corrupt, receipt.ok, (result.why, receipt))
                    self.assertEqual(0 if wrong_path else 1, len(writes))
                    if writes:
                        self.assertEqual("corrupt" if corrupt else "hello", writes[0].read_text())
                finally:
                    driver.close()

    def test_scoped_candidate_keeps_region_in_promoted_flow(self):
        from nlc.browser import BrowserDriver
        from nlc.general import run, candidates, reacquire
        from nlc.promote import candidate_from, to_flow
        driver = BrowserDriver()
        try:
            driver.goto("data:text/html," + quote('<section aria-label="billing"><input aria-label="name" value="Alice"></section><section aria-label="shipping"><input aria-label="name" value="Bob"></section>'))
            initial = driver.observe()["tree"]
            billing = next(c for c in candidates(initial) if c["container"] == "billing")
            def here(goal, cands, done):
                if done:
                    return {"done": True}
                return {"actions": [{"do": "read", "candidate": next(c for c in cands if c["container"] == "billing")}]}
            result = run(driver, "page", "read billing name", here)
            self.assertEqual(["Alice"], result.said)
            flow = to_flow(candidate_from(result, "page", "read billing name"), "billing")
            step = flow.steps[0]
            self.assertEqual("billing", step.binding.ancestor)
            self.assertEqual(billing["element_index"], step.selector.resolve(initial)[0])
            driver._page.locator('section').first.evaluate('el => el.remove()')
            self.assertIsNone(reacquire(billing, driver.observe()["tree"]))
        finally:
            driver.close()

    def test_navigation_key_uses_current_focus_and_reobserves(self):
        from nlc.browser import BrowserDriver
        from nlc.general import run
        driver = BrowserDriver()
        try:
            driver.goto("data:text/html," + quote('<input aria-label="first"><input aria-label="second">'))
            driver._page.locator('[aria-label=first]').focus()
            def here(goal, cands, done):
                if done:
                    return {"done": True}
                self.assertIn("press_key", cands[0]["can"])
                return {"actions": [{"do": "press_key", "key": "Tab", "candidate": cands[0]}]}
            result = run(driver, "page", "move focus", here)
            self.assertTrue(result.ok, result.why)
            self.assertTrue(result.visits[0].cut_short)
            self.assertEqual("second", driver._page.evaluate("document.activeElement.getAttribute('aria-label')"))
        finally:
            driver.close()

    def test_scroll_replans_before_reading_updated_dom(self):
        from nlc.browser import BrowserDriver
        from nlc.general import run
        driver = BrowserDriver()
        try:
            driver.goto("data:text/html," + quote('''<div style="height:100px;overflow:auto"
              onscroll="document.querySelector('input').value='reached'">
              <button>Anchor</button><div style="height:2000px"></div></div>
              <input aria-label="result" value="pending">'''))
            def here(goal, cands, done):
                if not done:
                    return {"actions": [{"do": "scroll", "candidate": next(c for c in cands if c["label"] == "Anchor"), "direction": "down", "pages": 2}], "expect": "Value: reached"}
                if len(done) == 1:
                    return {"actions": [{"do": "read", "candidate": next(c for c in cands if c["label"] == "result")}]}
                return {"done": True}
            result = run(driver, "page", "read after scroll", here)
            self.assertTrue(result.ok, result.why)
            self.assertEqual(["reached"], result.said)
            self.assertEqual(200, driver._page.locator('div').first.evaluate('el => el.scrollTop'))
        finally:
            driver.close()
