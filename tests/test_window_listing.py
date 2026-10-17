"""Real dialogs must survive capture-badge filtering and CLI presentation."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


class WindowListing(unittest.TestCase):
    def test_contained_dialog_without_close_is_not_a_badge(self):
        from nlc import driver
        nodes = {
            'parent': {'AXRole': 'AXWindow', 'AXCloseButton': 'close'},
            'file_panel': {'AXRole': 'AXWindow', 'AXSubrole': 'AXDialog', 'AXTitle': 'Open'},
            'badge': {'AXRole': 'AXWindow', 'AXSubrole': 'AXDialog', 'AXTitle': 'Window', 'AXIdentifier': '_NS:8'},
            'unknown_small': {'AXRole': 'AXWindow', 'AXSubrole': 'AXDialog', 'AXTitle': 'Window'},
            'focused_badge_shape': {'AXRole': 'AXWindow', 'AXSubrole': 'AXDialog', 'AXTitle': 'Window', 'AXIdentifier': '_NS:8', 'AXFocused': True},
            'tooltip': {'AXRole': 'AXHelpTag'},
        }
        frames = {key: (20, 20, 66, 20) for key in nodes}
        frames.update(parent=(0, 0, 800, 600), file_panel=(30, 30, 500, 350))
        with patch.object(driver, 'AXUIElementCreateApplication', return_value='app'), \
             patch.object(driver, '_attr', side_effect=lambda el, key: list(nodes) if el == 'app' and key == 'AXWindows' else nodes.get(el, {}).get(key)), \
             patch.object(driver, '_elem_frame', side_effect=frames.get):
            self.assertEqual(driver._real_windows(1), ['parent', 'file_panel', 'unknown_small', 'focused_badge_shape'])

    def test_missing_or_multiple_focus_does_not_hide_dialogs(self):
        from nlc.cli import focused_window_view
        tree = '0 Window  Title: Document\n  1 TextArea\n2 Window  Title: Open\n  3 Button  Title: Choose'
        self.assertEqual(focused_window_view(tree), tree)
        marked = tree.replace('Title: Document', 'Title: Document [focused]').replace('Title: Open', 'Title: Open [focused]')
        self.assertEqual(focused_window_view(marked), marked)

    @unittest.skipUnless(shutil.which('swiftc'), 'Swift compiler unavailable')
    def test_signed_filter_preserves_the_same_dialog_cases(self):
        # Compile the actual production filter with fake attribute reads. No AX,
        # helper connection, app launch, screen read or UI input is involved.
        source = (ROOT / 'helper/TactoHelper.swift').read_text()
        start = source.index('    return byRole.enumerated().filter', source.index('func realWindows('))
        end = source.index('\n}', start)
        body = source[start:end]
        harness = '''import Foundation
import CoreGraphics
let kAXSubroleAttribute = "AXSubrole", kAXTitleAttribute = "AXTitle"
let kAXIdentifierAttribute = "AXIdentifier", kAXCloseButtonAttribute = "AXCloseButton"
let kAXMainAttribute = "AXMain", kAXFocusedAttribute = "AXFocused"
let nodes: [[String: Any]] = [
 ["AXCloseButton": "close"],
 ["AXSubrole":"AXDialog", "AXTitle":"Open"],
 ["AXSubrole":"AXDialog", "AXTitle":"Window", "AXIdentifier":"_NS:8"],
 ["AXSubrole":"AXDialog", "AXTitle":"Window"],
 ["AXSubrole":"AXDialog", "AXTitle":"Window", "AXIdentifier":"_NS:8", "AXFocused":true],
 ["AXSubrole":"AXDialog", "AXTitle":"Window", "AXIdentifier":"_NS:8"]
]
func attr(_ element: Int, _ key: String) -> Any? { nodes[element][key] }
func str(_ element: Int, _ key: String) -> String { attr(element,key) as? String ?? "" }
func filtered(_ byRole: [Int], _ frames: [CGRect?]) -> [Int] {
''' + body + '''
}
let parent = CGRect(x:0,y:0,width:800,height:600)
let small = CGRect(x:20,y:20,width:66,height:20)
let panel = CGRect(x:30,y:30,width:500,height:350)
assert(filtered([0,1,2,3,4,5], [parent,panel,small,small,small,panel]) == [0,1,3,4,5])
'''
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder)
            (path / 'main.swift').write_text(harness)
            built = subprocess.run(['swiftc', str(path / 'main.swift'), '-o', str(path / 'check')], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(path / 'check')], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
