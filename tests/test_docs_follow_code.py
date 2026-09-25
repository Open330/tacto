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


class DocsFollowCodeTests(unittest.TestCase):
    def test_every_cli_command_is_in_the_readme(self):
        commands = re.findall(r'if cmd == "(\w+)"', read("nlc/cli.py"))
        readme = read("README.md")
        for cmd in commands:
            with self.subTest(cmd=cmd):
                self.assertRegex(readme, rf"nlc {cmd}\b")

    def test_every_replay_and_agent_argument_is_in_the_api_doc(self):
        from nlc.agent import Agent
        from nlc.flow import replay
        api = read("docs/api.md")
        for fn in (replay, Agent.__init__):
            for name in inspect.signature(fn).parameters:
                if name in ("self",) or name.startswith("_"):
                    continue
                with self.subTest(fn=fn.__qualname__, name=name):
                    self.assertIn(name, api)

    def test_every_decision_parameter_is_explained_where_recipes_are_written(self):
        from nlc.flow import FLOW_ACTIONS
        for doc in ("docs/recipes.md", "skill/SKILL.md", "docs/api.md"):
            text = read(doc)
            for name in FLOW_ACTIONS["decide"]:
                with self.subTest(doc=doc, name=name):
                    self.assertIn(name, text)

    def test_every_replay_error_is_explained(self):
        codes = set(re.findall(r'error="([a-z_]+)"', read("nlc/flow.py")))
        text = read("skill/SKILL.md") + read("docs/recipes.md")
        for code in sorted(codes):
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", text)

    def test_the_readme_links_every_document(self):
        readme = read("README.md")
        for doc in sorted((ROOT / "docs").glob("*.md")):
            with self.subTest(doc=doc.name):
                self.assertIn(f"docs/{doc.name}", readme)

    def test_the_readme_describes_the_indicator_that_exists(self):
        from nlc.overlay import LABEL
        readme = read("README.md")
        self.assertIn(LABEL, readme)
        self.assertNotIn("테두리가", readme)     # the outline was removed 2026-09-21
        self.assertNotIn("NLC1", readme)        # there is no synthetic input to mark


if __name__ == "__main__":
    unittest.main()
