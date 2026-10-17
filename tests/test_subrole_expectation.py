"""Unlabelled window buttons must be distinguished again at dispatch."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from tacto.flow import Selector
from tacto.driver import _line_attr, _elem_label, Driver
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]

class SubroleParsing(unittest.TestCase):
    def test_subrole_is_a_distinct_boundary_not_part_of_title_or_value(self):
        line='2 Button  Title: owned, Subrole: AXCloseButton, Value: 1 [disabled]'
        self.assertEqual('owned',Selector._attr(line,'Title'))
        self.assertEqual('AXCloseButton',Selector._attr(line,'Subrole'))
        self.assertEqual('1',Selector._attr(line,'Value'))
        self.assertEqual('owned',_line_attr(line,'Title'))
        self.assertEqual('AXCloseButton',_line_attr(line,'Subrole'))

    def test_named_labels_stay_unchanged_and_only_unlabelled_buttons_show_subrole(self):
        for title,role,expected in [('named','AXButton','Title: named'),
                                    ('','AXWindow',''),('','AXButton','Subrole: AXCloseButton')]:
            attrs={'AXTitle':title,'AXRole':role,'AXSubrole':'AXCloseButton'}
            with patch('tacto.driver._attr',side_effect=lambda e,k:attrs.get(k)), \
                 patch('ApplicationServices.AXUIElementCopyMultipleAttributeValues',side_effect=RuntimeError('fixture fallback')):
                self.assertEqual(expected,_elem_label('owned'))

    def test_direct_driver_reads_actual_subrole_independently_of_rendered_label(self):
        driver=Driver.__new__(Driver)
        expect={'role':'Button','attribute':'Subrole','label':'AXCloseButton','enabled':True}
        for subrole in ('AXCloseButton','AXMinimizeButton',None):
            attrs={'AXRole':'AXButton','AXSubrole':subrole,'AXEnabled':True}
            with patch.object(driver,'_element',return_value='owned'), \
                 patch('tacto.driver._attr',side_effect=lambda e,k:attrs.get(k)):
                value=driver._mismatch(SimpleNamespace(pid=42),{'element_index':2},expect)
            self.assertEqual(subrole=='AXCloseButton',value=='')

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'macOS Swift required')
class SubroleSwift(unittest.TestCase):
    def test_actual_mismatch_refuses_changed_missing_disabled_close_control(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        handler=source[source.index('func mismatch('):source.index('/// The same normalization',source.index('func mismatch('))]
        harness=r'''
import Foundation
import ApplicationServices
let element=AXUIElementCreateApplication(10001)
var subrole:String?="AXCloseButton",enabled=true
func modalSheet(_ pid:pid_t)->Bool {false}
func selectionDigest(_ pid:pid_t)->String {"unused"}
func str(_ el:AXUIElement,_ key:String)->String? {
 if key=="AXRole" {return "AXButton"};if key=="AXSubrole" {return subrole};return nil
}
func attr(_ el:AXUIElement,_ key:String)->AnyObject? {key=="AXEnabled" ? NSNumber(value:enabled):nil}
func normalizedLabel(_ text:String,attribute:String)->String {text}
func label(_ el:AXUIElement)->String {"owned"}
let expect:[String:Any]=["role":"Button","label":"AXCloseButton","attribute":"Subrole","enabled":true]
'''
        checks=r'''
precondition(mismatch(expect,element:element,pid:42)==nil)
subrole="AXMinimizeButton";precondition(mismatch(expect,element:element,pid:42) != nil)
subrole=nil;precondition(mismatch(expect,element:element,pid:42) != nil)
subrole="AXCloseButton";enabled=false;precondition(mismatch(expect,element:element,pid:42) != nil)
precondition(mismatch(expect,element:nil,pid:42) != nil)
print("SUBROLE_PASS")
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(harness+handler+checks)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual('SUBROLE_PASS',result.stdout.strip())

if __name__=='__main__':unittest.main()
