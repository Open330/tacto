import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class VroidDragSwiftTests(unittest.TestCase):
    def test_invalid_or_interrupted_requests_post_no_events(self):
        source=r'''import Foundation
import AppKit
import ApplicationServices
import Darwin
let el=AXUIElementCreateApplication(getpid())
// Slow drags have sub-point steps. Their integer deltas must sum to the
// endpoint displacement in both directions, without cumulative rounding loss.
for end in [CGPoint(x:20,y:-7),CGPoint(x:-20,y:7)] {
    var previous=CGPoint.zero
    var totalX:Int64=0
    var totalY:Int64=0
    for i in 1...60 {
        let point=CGPoint(x:end.x*Double(i)/60,y:end.y*Double(i)/60)
        let delta=vroidDragDelta(previous:previous,current:point)
        totalX += delta.0; totalY += delta.1; previous=point
    }
    precondition(totalX==Int64(end.x) && totalY==Int64(end.y))
}
// Transient non-target ownership must settle to the target. A persistent
// covering owner remains rejected; interruption propagates without input.
var clock=0.0
var reads=0
let settled=try vroidWaitForTargetHit(target:42,deadline:0.2,now:{clock},wait:{clock += $0},check:{},owner:{reads += 1; return reads<3 ? 99:42})
precondition(settled.0==42 && settled.1==3 && clock<=0.2)
clock=0;reads=0
let covered=try vroidWaitForTargetHit(target:42,deadline:0.1,now:{clock},wait:{clock += $0},check:{},owner:{reads += 1;return 99})
precondition(covered.0==99 && covered.1==3 && clock==0.1)
reads=0
do {
    _=try vroidWaitForTargetHit(target:42,deadline:1,now:{0},wait:{_ in preconditionFailure()},check:{throw NativeWindowInputError.refused("cancelled")},owner:{reads += 1;return 42})
    preconditionFailure("interruption swallowed")
} catch { precondition(reads==0) }
let path=[CGPoint(x:0,y:0),CGPoint(x:10,y:0),CGPoint(x:10,y:20)]
precondition(vroidStrokePoint(path,fraction:1.0/3.0)==CGPoint(x:10,y:0))
precondition(vroidStrokePoint(path,fraction:2.0/3.0)==CGPoint(x:10,y:10))
precondition(vroidStrokePoint(path,fraction:1)==CGPoint(x:10,y:20))
precondition(vroidStrokePoint([CGPoint(x:3,y:4),CGPoint(x:3,y:4)],fraction:0.5)==CGPoint(x:3,y:4))
precondition(vroidDragTitleMatches("Trial.vroid*", expected:"Trial.vroid"))
precondition(vroidDragTitleMatches("Trial.vroid*", expected:"Trial.vroid*"))
precondition(!vroidDragTitleMatches("Other.vroid*", expected:"Trial.vroid"))
precondition(!vroidDragTitleMatches("Trial.vroid", expected:"Trial.vroid*"))
precondition(!vroidDragTitleMatches("Trial.vroid**", expected:"Trial.vroid"))
let size=CGSize(width:1500,height:900)
let graph=CGRect(x:1288,y:425,width:200,height:86)
precondition(vroidDragRegion(windowSize:size,graphBounds:graph)==graph)
precondition(vroidDragRegion(windowSize:size,graphBounds:nil)!.contains(CGPoint(x:800,y:500)))
precondition(!vroidDragRegion(windowSize:size,graphBounds:nil)!.contains(CGPoint(x:1288,y:500)))
for bad in [CGRect(x:50,y:425,width:200,height:86),CGRect(x:1288,y:425,width:220,height:86),
            CGRect(x:1288,y:425,width:200,height:121),CGRect(x:1288,y:20,width:200,height:86),
            CGRect(x:1288,y:820,width:200,height:86),CGRect(x:1288,y:425,width:-2,height:86)] {
    precondition(vroidDragRegion(windowSize:size,graphBounds:bad)==nil)
}
let binding=NativeWindowBinding(element:el,window:el,pid:getpid(),processStart:1,windowID:17,
    windowBounds:CGRect(x:0,y:0,width:1500,height:900),elementBounds:CGRect(x:0,y:0,width:1500,height:900),point:CGPoint(x:800,y:500))
for mode in ["duration","nonfinite","title","panel","deadline","context","via_panel","via_nonfinite","via_many"] {
    let from=mode=="panel" ? CGPoint(x:50,y:50):CGPoint(x:800,y:500)
    let duration=mode=="duration" ? 4.0:mode=="nonfinite" ? Double.nan:1.0
    let title=mode=="title" ? "wrong":"Trial.vroid"
    let deadline=ProcessInfo.processInfo.systemUptime+(mode=="deadline" ? 0.1:5)
    var checks=0
    let via=mode=="via_panel" ? [CGPoint(x:50,y:50)] : mode=="via_nonfinite" ? [CGPoint(x:Double.nan,y:500)] : mode=="via_many" ? Array(repeating:CGPoint(x:810,y:490),count:13) : []
    let reply=dispatchVroidDrag(binding,title:title,from:from,to:CGPoint(x:820,y:470),duration:duration,
        via:via,
        deadline:deadline,stopped:{false},context:{checks+=1;throw NativeWindowInputError.refused("fixture context stopped")})
    precondition(reply["type"] as? String=="error")
    precondition(reply["effect"] as? String=="not_started")
    precondition(reply["posted_or_attempted_events"] as? Int==0)
    precondition(checks==(mode=="context" ? 1:0))
}
for mode in ["title", "deadline", "stopped", "context"] {
    var checks=0
    let reply=dispatchVroidFocus(binding,title:mode=="title" ? "Calculator":"VRoid Studio",
        deadline:ProcessInfo.processInfo.systemUptime+(mode=="deadline" ? 0.1:5),
        stopped:{mode=="stopped"},context:{checks+=1;throw NativeWindowInputError.refused("fixture focus stopped")})
    precondition(reply["type"] as? String=="error")
    precondition(reply["effect"] as? String=="not_started")
    precondition(reply["posted_events"] as? Int==0)
    precondition(checks==(mode=="context" ? 1:0))
}
print("VROID_GUARDS_PASS")
'''
        with tempfile.TemporaryDirectory() as root:
            main=Path(root)/"main.swift";exe=Path(root)/"check";main.write_text(source)
            built=subprocess.run(["xcrun","swiftc",str(main),str(ROOT/"helper/WindowInput.swift"),str(ROOT/"helper/FrontmostProcess.swift"),str(ROOT/"helper/experimental/VroidDrag.swift"),"-o",str(exe)],capture_output=True,text=True,timeout=90)
            self.assertEqual(0,built.returncode,built.stderr)
            ran=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
            self.assertEqual(0,ran.returncode,ran.stderr)
            self.assertEqual("VROID_GUARDS_PASS",ran.stdout.strip())
