"""Actual window event construction; injected posting never sends UI input."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift required')
class ForegroundClickTests(unittest.TestCase):
    def test_click_focus_transition_is_owned_and_never_hides_takeover(self):
        harness=r'''
import Foundation
import AppKit
import ApplicationServices
let element=AXUIElementCreateApplication(10001),window=AXUIElementCreateApplication(10002)
let focus=AXUIElementCreateApplication(10003),clicked=AXUIElementCreateApplication(10004),outside=AXUIElementCreateApplication(10005)
let binding=NativeWindowBinding(element:element,window:window,pid:42,processStart:10,
 windowID:17,windowBounds:CGRect(x:100,y:200,width:300,height:200),
 elementBounds:CGRect(x:110,y:230,width:100,height:40),point:CGPoint(x:160,y:250))
for mode in ["stable","focus_after_down","navigation_after_up","outside_focus","pointer","foreground","stopped","target_changed","read_failed","unsupported_double","no_focus_validator","between_events"] {
 var posted=0,reads=0,stop=false
 do {
  let result=try dispatchNativeWindowPreparation(binding,deadline:100,stopped:{stop},context:{},now:{0},
   readInput:{_ in
    reads+=1
    if mode=="read_failed" && posted>0 {throw NativeWindowInputError.unavailable("focus unreadable")}
    let selected:AXUIElement = mode=="outside_focus" && posted>0 ? outside
      : mode=="focus_after_down" && posted>0 || mode=="navigation_after_up" && posted==2 ? clicked
      : mode=="between_events" && reads>=5 ? outside : focus
    return NativeUserInputState(foreground:mode=="foreground" && posted>0 ? 77:42,
      pointer:mode=="pointer" && posted>0 ? CGPoint(x:2,y:3):.zero,focused:selected)
   },validate:{_ in
    if mode=="target_changed" && posted>0 || mode=="navigation_after_up" && posted==2 {
      throw NativeWindowInputError.refused("old target disappeared")
    }
   },post:{event,pid in
    precondition(pid==42 && event.type==(posted==0 ? .leftMouseDown:.leftMouseUp));posted+=1
    if mode=="stopped" {stop=true}
   },routing:{_,_ in},elementClicks:mode=="unsupported_double" ? 2:1,
   foregroundClickFocus:mode=="no_focus_validator" ? nil : {_,value in
    guard let value,!CFEqual(value,outside) else {throw NativeWindowInputError.refused("focus outside bound window")}
   })
  precondition(["stable","focus_after_down","navigation_after_up"].contains(mode),mode)
  precondition(result.posted==2 && posted==2 && result.foreground==42)
 } catch let failure as NativeWindowDispatchFailure {
  precondition(!["stable","focus_after_down","navigation_after_up"].contains(mode),"unexpected refusal: \(mode): \(failure.detail)")
  let expected=mode=="unsupported_double" || mode=="no_focus_validator" ? 0:1
  precondition(posted==expected && failure.attempted==expected,"unexpected post count: \(mode): \(posted)")
 } catch {fatalError("unexpected error: \(error)")}
}
print("FOREGROUND_CLICK_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-foreground-click-') as directory:
            source=Path(directory)/'main.swift';binary=Path(directory)/'check';source.write_text(harness)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(source),str(ROOT/'helper/WindowInput.swift'),str(ROOT/'helper/WindowDispatch.swift'),str(ROOT/'helper/FrontmostProcess.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=90)
            self.assertEqual(0,built.returncode,built.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual('FOREGROUND_CLICK_PASS',result.stdout.strip())
