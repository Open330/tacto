"""Nested modality, malformed AX replies and budgets never authorize input."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from tacto.modal_presence import modal_presence

ROOT=Path(__file__).resolve().parents[1]


class ModalPresenceTests(unittest.TestCase):
    def query(self, graph, **options):
        def read(node,key):
            value=graph.get((node,key),(-25205,None))
            if isinstance(value,Exception):raise value
            return value
        return modal_presence(0,read,is_element=lambda x:type(x) is int and x>=0,**options)

    def base(self):
        return {(0,'AXWindows'):(0,[1]),(1,'AXRole'):(0,'AXWindow'),
                (1,'AXChildren'):(0,[2]),(2,'AXRole'):(0,'AXButton')}

    def test_clear_present_nested_and_modal_attribute(self):
        graph=self.base();self.assertEqual(self.query(graph),'clear')
        graph[2,'AXRole']=(0,'AXSheet');self.assertEqual(self.query(graph),'present')
        graph=self.base();graph[2,'AXModal']=(0,True);self.assertEqual(self.query(graph),'present')
        graph=self.base();graph[1,'AXSheets']=(0,[3]);self.assertEqual(self.query(graph),'present')
        graph=self.base();graph[2,'AXRole']=(0,'AXGroup');graph[2,'AXChildren']=(0,[3]);graph[3,'AXRole']=(0,'AXSheet')
        graph[0,'AXFocusedUIElement']=(0,3)
        self.assertEqual(self.query(graph),'present')

    def test_optional_no_value_is_absence_but_required_no_value_is_unobservable(self):
        for key in ((1,'AXModal'),(1,'AXSheets'),(2,'AXModal'),(2,'AXSheets')):
            graph=self.base();graph[key]=(-25212,None)
            self.assertEqual(self.query(graph),'clear')
        for key in ((0,'AXWindows'),(1,'AXRole'),(1,'AXChildren')):
            graph=self.base();graph[key]=(-25212,None)
            self.assertEqual(self.query(graph),'unobservable')

    def test_read_errors_malformed_values_and_unreadable_containers_are_not_clear(self):
        for key,reply in (((0,'AXWindows'),(-25204,None)),((0,'AXWindows'),(0,None)),
                ((0,'AXWindows'),(False,[])),((0,'AXWindows'),(0,[True])),
                ((1,'AXRole'),(0,None)),((1,'AXModal'),(0,1)),
                ((1,'AXModal'),(-25204,None)),((1,'AXSheets'),(0,None)),
                ((0,'AXFocusedUIElement'),(-25204,None)),((0,'AXFocusedUIElement'),(0,False)),
                ((1,'AXChildren'),(-25205,None)),((1,'AXChildren'),(0,'private')),
                ((1,'AXChildren'),RuntimeError('PRIVATE_SENTINEL'))):
            graph=self.base();graph[key]=reply
            with self.subTest(key=key,reply=reply):self.assertEqual(self.query(graph),'unobservable')

    def test_capacity_depth_and_cycle_are_bounded_without_prefix_pruning(self):
        graph=self.base();graph[2,'AXRole']=(0,'AXGroup');graph[2,'AXChildren']=(0,[2])
        graph[0,'AXFocusedUIElement']=(0,2);graph[2,'AXParent']=(0,2)
        self.assertEqual(self.query(graph,max_depth=3),'unobservable')
        self.assertEqual(self.query(self.base(),max_nodes=1),'unobservable')
        graph=self.base();graph[1,'AXChildren']=(0,list(range(2,82)))
        for node in range(2,82):graph[node,'AXRole']=(0,'AXButton')
        graph[81,'AXRole']=(0,'AXSheet')
        self.assertEqual(self.query(graph),'present')

    def test_large_content_is_not_rewalked_and_focus_chain_catches_nested_sheet(self):
        graph=self.base();graph[2,'AXRole']=(0,'AXGroup')
        graph[2,'AXChildren']=(0,list(range(3,6003)))
        calls=[]
        def read(node,key):
            calls.append((node,key));return graph.get((node,key),(-25205,None))
        self.assertEqual(modal_presence(0,read,is_element=lambda x:type(x) is int),'clear')
        self.assertNotIn((2,'AXChildren'),calls)
        self.assertLess(len(calls),12)
        graph[0,'AXFocusedUIElement']=(0,4);graph[4,'AXRole']=(0,'AXButton')
        graph[4,'AXParent']=(0,3);graph[3,'AXRole']=(0,'AXSheet')
        self.assertEqual(modal_presence(0,read,is_element=lambda x:type(x) is int),'present')

    def test_real_direct_adapter_enforces_timeout_and_reset_failures(self):
        from tacto import driver
        def read(node,key,unused):
            if key=='AXWindows':return 0,[1]
            if key=='AXRole':return 0,'AXWindow'
            if key=='AXChildren':return 0,[]
            return -25205,None
        for timeouts,expected in (([0]*20,False),([1],True),([0,1],True)):
            with patch.object(driver,'AXUIElementCreateApplication',return_value=0), \
                    patch.object(driver,'AXUIElementCopyAttributeValue',side_effect=read), \
                    patch.object(driver,'AXUIElementSetMessagingTimeout',side_effect=timeouts) as timeout, \
                    patch.object(driver,'CFGetTypeID',return_value=99), \
                    patch.object(driver,'AXUIElementGetTypeID',return_value=99):
                self.assertEqual(driver.Driver._modal_sheet(42),expected)
                if expected:self.assertLessEqual(timeout.call_count,2)


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'macOS Swift required')
class NativeModalPresenceTests(unittest.TestCase):
    def test_actual_swift_checker_matches_nested_error_and_budget_cases(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        core=source[source.index('enum NativeModalPresence:'):source.index('func modalSheet(')]
        harness=r'''import Foundation
import ApplicationServices
let app=AXUIElementCreateApplication(90001), window=AXUIElementCreateApplication(90002)
let group=AXUIElementCreateApplication(90003), sheet=AXUIElementCreateApplication(90004)
var nested=true, bad=false, deny=false
func read(_ node:AXUIElement,_ key:String)throws->(AXError,CFTypeRef?) {
 if CFEqual(node,app) {
  if key=="AXWindows" {return (.success,[window] as CFArray)}
  if key=="AXFocusedUIElement" {return nested ? (.success,sheet) : (.noValue,nil)}
 }
 if key=="AXRole" {return (.success,(CFEqual(node,window) ? "AXWindow" : CFEqual(node,group) ? "AXGroup" : "AXSheet") as CFString)}
 if key=="AXChildren" {
  if bad {return (.cannotComplete,nil)}
  if deny {return (.attributeUnsupported,nil)}
  return (.success,(CFEqual(node,window) ? [group] : nested ? [sheet] : []) as CFArray)
 }
 return (.attributeUnsupported,nil)
}
'''
        cases=r'''
var out:[String:String]=[:]
out["nested"]=nativeObservedModalPresence(app,read:read).rawValue
nested=false;out["clear"]=nativeObservedModalPresence(app,read:read).rawValue
bad=true;out["read_error"]=nativeObservedModalPresence(app,read:read).rawValue
bad=false;deny=true;out["unreadable_container"]=nativeObservedModalPresence(app,read:read).rawValue
deny=false;out["budget"]=nativeObservedModalPresence(app,maxNodes:1,read:read).rawValue
print(String(data:try! JSONSerialization.data(withJSONObject:out),encoding:.utf8)!)
'''
        result=subprocess.run(['swift','-e',harness+core+cases],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'nested':'present','clear':'clear','read_error':'unobservable',
            'unreadable_container':'unobservable','budget':'unobservable'})
