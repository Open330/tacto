"""The documents say what the code does — checked, not hoped.

The README described a window outline that had been replaced by a menu-bar
item, and a synthetic-input marker for input the driver no longer makes; a
whole session of features (decisions, branches, promotion) was in no
document at all. These tests fail when a public name is added without
being documented.
"""
import inspect
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def skill_stage_text():
    """Check reachable skill stages without requiring every detail in SKILL.md."""
    pending=[ROOT/'skill/SKILL.md'];seen=set();texts=[]
    reference_root=(ROOT/'skill/references').resolve()
    while pending:
        path=pending.pop().resolve()
        if path in seen:continue
        seen.add(path);text=path.read_text();texts.append(text)
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
            target=target.split('#',1)[0]
            if not target or '://' in target:continue
            linked=(path.parent/target).resolve()
            if linked.suffix=='.md' and linked.is_relative_to(reference_root):
                pending.append(linked)
    return '\n'.join(texts)


class DocsFollowCodeTests(unittest.TestCase):
    def test_every_cli_command_is_in_the_readme(self):
        commands = re.findall(r'if cmd == "(\w+)"', read("tacto/cli.py"))
        readme = read("README.md")
        for cmd in commands:
            with self.subTest(cmd=cmd):
                self.assertRegex(readme, rf"tacto {cmd}\b")

    def test_every_replay_and_agent_argument_is_in_the_api_doc(self):
        from tacto.agent import Agent
        from tacto.flow import replay
        api = read("docs/api.md")
        for fn in (replay, Agent.__init__):
            for name in inspect.signature(fn).parameters:
                if name in ("self",) or name.startswith("_"):
                    continue
                with self.subTest(fn=fn.__qualname__, name=name):
                    self.assertIn(name, api)

    def test_every_decision_parameter_is_explained_where_recipes_are_written(self):
        from tacto.flow import FLOW_ACTIONS
        for doc in ("docs/recipes.md", "skill/SKILL.md", "docs/api.md"):
            text = skill_stage_text() if doc=="skill/SKILL.md" else read(doc)
            for name in FLOW_ACTIONS["decide"]:
                with self.subTest(doc=doc, name=name):
                    self.assertIn(name,text,f"{name} is missing from {doc} and its reachable stages")

    def test_every_replay_error_is_explained(self):
        codes = set(re.findall(r'error="([a-z_]+)"', read("tacto/flow.py")))
        text = skill_stage_text() + read("docs/recipes.md")
        for code in sorted(codes):
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", text)

    def test_the_readme_links_every_document(self):
        readme = read("README.md")
        for doc in sorted((ROOT / "docs").glob("*.md")):
            with self.subTest(doc=doc.name):
                self.assertIn(f"docs/{doc.name}", readme)

    def test_the_readme_describes_the_indicator_that_exists(self):
        readme = read("README.md")
        self.assertIn("existing Tacto menu bar icon", readme)
        self.assertIn("작업 중 배너 표시", readme)
        self.assertNotIn("NSStatusBar.system.statusItem",read("helper/TactoHelper.swift"))
        self.assertNotIn("테두리가", readme)     # the outline was removed 2026-09-21
        self.assertNotIn("TACTO1", readme)        # there is no synthetic input to mark


if __name__ == "__main__":
    unittest.main()
