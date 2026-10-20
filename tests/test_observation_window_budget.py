"""Run the native walker and observation loop against oversized window trees."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def parents(lines, elements):
    stack, result = [], {}
    for line in lines:
        if not line.lstrip().split()[0].isdigit():
            continue
        depth = len(line) - len(line.lstrip())
        node = elements[int(line.split()[0])]
        while stack and stack[-1][0] >= depth:
            stack.pop()
        result[node] = stack[-1][1] if stack else None
        stack.append((depth, node))
    return result


class PythonObservationWindowBudgetTests(unittest.TestCase):
    def test_resuming_nested_frontier_preserves_parents_and_click_ancestor(self):
        from tacto import driver
        from tacto.flow import Selector
        graph = {0:[1, 6], 1:[2, 3, 4, 5], 6:[7, 8]}
        results = []
        for budget in (12, 100):
            reads = []
            def attr(node, key):
                if key == 'AXChildren':
                    reads.append(node)
                    return graph.get(node, [])
                if key == 'AXRole':
                    return 'AXWindow' if node in (0, 400, 500) else 'AXGroup' if node in (1, 6) else 'AXButton'
                return True if key == 'AXEnabled' else None
            obs = driver.Observation.__new__(driver.Observation)
            obs.pid, obs.elements, obs.lines = 1, [], []
            obs.visited, obs.truncated = 0, False
            with patch.object(driver, 'MAX_TREE_NODES', budget), \
                 patch.object(driver, '_real_windows', return_value=[0, 400, 500]), \
                 patch.object(driver, '_attr', side_effect=attr), \
                 patch.object(driver, '_elem_label', side_effect=lambda node, **kw:
                              'Title: ' + (json.dumps(f'fixture-{node}') if node in (0, 400, 500)
                                           else f'fixture-{node}')), \
                 patch.object(driver, '_secondary_actions', return_value=[]):
                driver.Driver._walk_windows(None, obs)
            self.assertFalse(obs.truncated)
            self.assertEqual(len(reads), len(set(reads)))
            tree = '\n'.join(obs.lines)
            for node, group in ((4, 1), (7, 6)):
                index = Selector(role='Button', title=f'fixture-{node}',
                                 ancestor=f'fixture-{group}', click_ancestor=['Group']).resolve(tree)[0]
                self.assertEqual(group, obs.elements[index])
            with self.assertRaises(LookupError):
                Selector(role='Button', title='fixture-4', ancestor='fixture-6').resolve(tree)
            results.append(parents(obs.lines, obs.elements))
        self.assertEqual(results[0], results[1])

    def test_large_first_window_reserves_later_roots_and_focus_markers(self):
        from tacto import driver
        cases = (([0, 200, 300], 100), ([0], 100), (list(range(200, 213)), 100),
                 ([], 100), ([0, 400, 500], 6))
        for windows, child_count in cases:
            with self.subTest(windows=windows):
                roots = set(windows)
                reads = []
                def attr(node, key):
                    if key == 'AXRole':
                        return 'AXWindow' if node in roots else 'AXButton'
                    if key == 'AXChildren':
                        reads.append(node)
                        return {0:list(range(1, child_count + 1)), 200:list(range(201, 206)),
                                300:[301, 302]}.get(node, [])
                    if key == 'AXFocused':
                        return node == 300
                    return True if key == 'AXEnabled' else None
                obs = driver.Observation.__new__(driver.Observation)
                obs.pid, obs.elements, obs.lines = 1, [], []
                obs.visited, obs.truncated = 0, False
                with patch.object(driver, 'MAX_TREE_NODES', 12), \
                     patch.object(driver, 'MAX_CHILDREN_PER_NODE', 100), \
                     patch.object(driver, '_real_windows', return_value=windows), \
                     patch.object(driver, '_attr', side_effect=attr), \
                     patch.object(driver, '_elem_label', side_effect=lambda node, **kw:
                                  'Title: ' + (json.dumps(f'fixture-{node}') if node in roots
                                               else f'fixture-{node}')), \
                     patch.object(driver, '_secondary_actions', return_value=[]):
                    present = driver.Driver._walk_windows(None, obs)
                self.assertEqual(bool(windows), present)
                self.assertLessEqual(obs.visited, 12)
                self.assertTrue(set(windows[:12]).issubset(obs.elements))
                self.assertEqual(bool(windows) and child_count == 100, obs.truncated)
                self.assertEqual(list(range(len(obs.elements))),
                                 sorted(int(line.split()[0]) for line in obs.lines
                                        if line.split()[0].isdigit()))
                self.assertEqual(len(reads), len(set(reads)))
                if child_count == 6:
                    self.assertEqual(set([0, *range(1, 7), 400, 500]), set(obs.elements))
                if 300 in windows:
                    self.assertTrue(any(line.startswith(f'{obs.elements.index(300)} Window')
                                        and line.endswith('[focused]') for line in obs.lines))
                    self.assertTrue({201, 202, 203, 301, 302}.issubset(obs.elements))
                    from tacto.flow import Selector
                    tree = '\n'.join(obs.lines)
                    for node, parent in ((4, 0), (201, 200), (301, 300)):
                        self.assertEqual(obs.elements.index(node),
                                         Selector(role='Button', title=f'fixture-{node}',
                                                  ancestor=f'fixture-{parent}').resolve(tree)[0])


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class ObservationWindowBudgetTests(unittest.TestCase):
    def test_later_roots_survive_large_first_window_without_exceeding_budget(self):
        source = (ROOT / 'helper/TactoHelper.swift').read_text()
        walker = source[source.index('func walk(_ el: AXUIElement, depth: Int, out:'):]
        walker = walker[:walker.index('\n}\n') + 3]
        observe = source[source.index('func observe(_ target:'):]
        loop = observe[observe.index('    var lines: [String] = []'):observe.index('    let obsID =')]
        script = '''import Foundation
typealias AXUIElement = Int
let maxTreeNodes = 12
let maxChildrenPerNode = 100
let kAXRoleAttribute = "AXRole"
let kAXWindowRole = "AXWindow"
let kAXEnabledAttribute = "AXEnabled"
let kAXSelectedAttribute = "AXSelected"
let kAXChildrenAttribute = "AXChildren"
let interactableRoles: Set<String> = ["AXWindow", "AXButton"]
var roots: Set<Int> = []
var childReads: [Int] = []
var childCount = 100
var childGraph: [Int:[Int]]? = nil
struct ObservationNodeSnapshot {
 init?(_ node:Int) { return nil }
 func string(_ name:String)->String? { nil }
 func value(_ name:String)->Any? { nil }
 func label(role:String,quoteValues:Bool)->String { "" }
}
final class ObservationReadMetrics {
 var packetAttempts=0,packetFallbacks=0,secondaryActionReads=0
}
func str(_ node: Int, _ key: String) -> String? {
 key == "AXRole" ? (roots.contains(node) ? "AXWindow" :
  childGraph != nil && [1,6].contains(node) ? "AXGroup" : "AXButton") : nil
}
func label(_ node: Int, quoteValues: Bool) -> String { "fixture-\\(node)" }
func attr(_ node: Int, _ key: String) -> Any? {
 if key == "AXChildren" {
  childReads.append(node)
  if let childGraph { return childGraph[node] ?? [] }
  return node == 0 ? Array(1...childCount) : node == 200 ? Array(201...205) :
         node == 300 ? [301,302] : []
 }
 return key == "AXEnabled" ? true : false
}
func secondaryActionsOf(_ node: Int) -> [String] { [] }
func documentURLSyntaxPath(_ text: String) -> String? { nil }
func quotedLabelValue(_ text: String) -> String? { nil }
''' + walker + '''
func observation(_ windows: [Int]) -> [String: Any] {
 roots = Set(windows); childReads = []
''' + loop + '''
 return ["visited":visited, "elements":elements, "lines":lines,
         "truncated":truncated, "child_reads":childReads]
}
var rows = [observation([0, 200, 300]), observation([0]),
            observation(Array(200..<213)), observation([])]
childCount = 6
rows.append(observation([0, 400, 500]))
childGraph = [0:[1,6], 1:[2,3,4,5], 6:[7,8]]
rows.append(observation([0, 400, 500]))
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-window-budget-') as directory:
            path = Path(directory) / 'main.swift'
            path.write_text(script)
            run = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        multiple, single, too_many, empty, reclaimed, nested = json.loads(run.stdout)
        self.assertEqual([0, 1, 2, 3, 200, 201, 202, 203, 300, 301, 302, 4], multiple['elements'])
        self.assertEqual([0, *range(1, 12)], single['elements'])
        self.assertEqual(list(range(200, 212)), too_many['elements'])
        self.assertEqual([], empty['elements'])
        for row in (multiple, single, too_many, empty, reclaimed, nested):
            self.assertLessEqual(row['visited'], 12)
            self.assertEqual(len(row['elements']), row['visited'])
            self.assertEqual(list(range(len(row['elements']))),
                             sorted(int(line.split()[0]) for line in row['lines']
                                    if line.split()[0].isdigit()))
            self.assertEqual(len(row['child_reads']), len(set(row['child_reads'])))
        self.assertTrue(multiple['truncated'])
        self.assertTrue(single['truncated'])
        self.assertTrue(too_many['truncated'])
        self.assertFalse(empty['truncated'])
        self.assertFalse(reclaimed['truncated'])
        self.assertEqual(set([0, *range(1, 7), 400, 500]), set(reclaimed['elements']))
        self.assertFalse(nested['truncated'])
        self.assertEqual({0:None, 1:0, 2:1, 3:1, 4:1, 5:1, 6:0, 7:6, 8:6,
                          400:None, 500:None}, parents(nested['lines'], nested['elements']))


if __name__ == '__main__':
    unittest.main()
