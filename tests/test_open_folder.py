"""open_folder: Finder shows a folder without activating (signed helper only)."""
import json
import pathlib
import unittest

from nlc.flow import Flow
from nlc.protocol import ACTION_PARAMS

ROOT = pathlib.Path(__file__).resolve().parents[1]


class OpenFolderTests(unittest.TestCase):
    def test_a_flow_with_open_folder_loads(self):
        flow = Flow.from_json(json.dumps({"schema": 2, "name": "f", "target": "com.apple.finder",
            "steps": [{"action": "open_folder", "params": {"path": "/tmp/x"},
                       "postcondition": {"field": {"role": "Window", "title": "x"},
                                         "attribute": "Title", "equals": "x"},
                       "safety": {"effects": ["write_ephemeral"],
                                  "resource": {"object_type": "Finder window", "scope": "item"},
                                  "reversibility": "reversible"}}]}))
        self.assertEqual("open_folder", flow.steps[0].action)
        self.assertEqual(frozenset({"path"}), ACTION_PARAMS["open_folder"])

    def test_helper_scope(self):
        swift = (ROOT / 'helper/NLCHelper.swift').read_text()
        case = swift[swift.index('    case "open_folder":'):swift.index('    case "select_option":')]
        for needed in ('target.lowercased() == "com.apple.finder"', 'raw.hasPrefix("/")',
                       'isDirectory.boolValue', 'kind.isPackage == false', 'kind.isApplication != true',
                       'configuration.activates = false', 'configuration.addsToRecentItems = false',
                       'opened["activated_target"] = true'):
            self.assertIn(needed, case)
        self.assertNotIn('"unverified"', case)   # the flow's postcondition decides

    def test_launcher_accepts_only_an_absolute_path(self):
        launcher = (ROOT / 'launcher/nlc.swift').read_text()
        self.assertIn('"open_folder":      (["name", "path"], []),', launcher)
        self.assertIn('if name == "open_folder" {', launcher)
        self.assertIn('text.hasPrefix("/")', launcher)


if __name__ == '__main__':
    unittest.main()
