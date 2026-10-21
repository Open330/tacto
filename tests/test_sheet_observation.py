"""Unlabelled sheets must remain visible to the actual recovery predicate."""
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
from tacto.flow import Selector, Postcondition

ROOT = Path(__file__).resolve().parents[1]


def check_sheet(test, tree):
    test.assertFalse(Postcondition(absent_role='Sheet').check(tree))
    test.assertIn('  1 Sheet\n',tree+'\n')
    test.assertEqual(Selector(role='Button',title='Cancel').resolve(tree)[0],2)


class SheetObservationTests(unittest.TestCase):
    def test_sheet_labels_never_impersonate_sheet_role(self):
        from tacto.library import close_dialog
        from tacto.flow import Flow
        for title in ('Sheet1','Spreadsheet','Share Sheet'):
            tree=f'0 Window Title: {title}\n  1 Button Title: Cancel'
            step=close_dialog().steps[0]
            self.assertTrue(step.skip_if.check(tree))
            self.assertTrue(step.postcondition.check(tree))
            roundtrip=Flow.from_json(close_dialog().to_json()).steps[0]
            self.assertEqual(roundtrip.skip_if.absent_role,'Sheet')
            self.assertTrue(roundtrip.skip_if.check(tree))
        self.assertFalse(Postcondition(absent_role='Sheet').check('0 Window Title: Work\n  1 Sheet'))
        self.assertFalse(Postcondition(absent_role='AXSheet').check('0 Window Title: Work\n  1 Sheet'))
        self.assertTrue(Postcondition(absent_role='Sheet').check('0 Window Title: Work\n  1 Spreadsheet'))

    def test_invalid_role_absence_is_rejected_when_loading_ir(self):
        from tacto.flow import Flow
        from tacto.library import close_dialog
        import json
        for value in (True,[],{},0,''):
            raw=json.loads(close_dialog().to_json())
            raw['steps'][0]['postcondition']['absent_role']=value
            with self.assertRaises(ValueError):Flow.from_json(json.dumps(raw))

    def test_authoring_keeps_role_absence_and_rejects_invalid_values(self):
        from tacto.authoring import _post
        condition = _post({'absent_role':'Sheet'})
        self.assertEqual(condition.absent_role,'Sheet')
        self.assertTrue(condition.check('0 Window Title: Sheet1'))
        self.assertFalse(condition.check('0 Window Title: Work\n  1 Sheet'))
        for value in (True,[],{},0,''):
            with self.assertRaises(ValueError):_post({'absent_role':value})
    def test_python_walker_preserves_unlabelled_sheet_boundary(self):
        from tacto import driver
        obs = driver.Observation.__new__(driver.Observation)
        obs.elements, obs.lines, obs.visited, obs.truncated = [],[],0,False
        def attr(node,key):
            if key=='AXRole':return ['AXWindow','AXSheet','AXButton'][node]
            if key=='AXChildren':return {0:[1],1:[2]}.get(node,[])
            if key=='AXEnabled':return True
        with patch.object(driver,'_attr',side_effect=attr), \
                patch.object(driver,'_elem_label',side_effect=lambda node,**kw: {0:'Title: Work',2:'Title: Cancel'}.get(node,'')), \
                patch.object(driver,'_secondary_actions',return_value=[]):
            obs.walk(0)
        check_sheet(self,'\n'.join(obs.lines))

    @unittest.skipUnless(shutil.which('swift'),'Swift required')
    def test_swift_actual_walker_and_role_table_preserve_same_sheet(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        roles=source[source.index('let interactableRoles:'):]
        roles=roles[:roles.index(']')+1]
        walker=source[source.index('func walk(_ el: AXUIElement, depth: Int, out:'):]
        walker=walker[:walker.index('// MARK: - Session state')]
        preamble='''import Foundation
typealias AXUIElement = Int
let kAXRoleAttribute = "AXRole", kAXWindowRole = "AXWindow", kAXEnabledAttribute = "AXEnabled"
let kAXSelectedAttribute = "AXSelected", kAXChildrenAttribute = "AXChildren"
let maxTreeNodes = 2500, maxChildrenPerNode = 60
struct ObservationNodeSnapshot {
 init?(_ node:Int) { return nil }
 func string(_ name:String)->String? { nil }
 func value(_ name:String)->Any? { nil }
 func label(role:String,quoteValues:Bool)->String { "" }
}
final class ObservationReadMetrics {
 var packetAttempts=0,packetFallbacks=0,secondaryActionReads=0
}
final class ObservationRevealScan {
 var complete=true
 var revealed=0
 var available:Bool { false }
 func matches(_ node:Int)->Bool { false }
}
func str(_ el: Int, _ key: String) -> String? {
    return key == "AXRole" ? ["AXWindow","AXSheet","AXButton"][el] : nil
}
func attr(_ el: Int, _ key: String) -> Any? {
    if key == "AXChildren" { return [0:[1],1:[2]][el] ?? [] }
    return key == "AXEnabled" ? true : nil
}
func label(_ el: Int, quoteValues: Bool) -> String { [0:"Title: Work",2:"Title: Cancel"][el] ?? "" }
func secondaryActionsOf(_ el: Int) -> [String] { [] }
func documentURLSyntaxPath(_ value: String) -> String? { nil }
func quotedLabelValue(_ value: String) -> String? { nil }
'''
        code=preamble+roles+'\n'+walker+'''
var lines: [String] = [], elements: [Int] = []
var visited = 0, truncated = false
walk(0, depth: 0, out: &lines, elements: &elements, visited: &visited, truncated: &truncated)
print(lines.joined(separator: "\\n"))
'''
        result=subprocess.run(['swift','-e',code],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        check_sheet(self,result.stdout)


if __name__=='__main__':unittest.main()
