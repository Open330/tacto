"""Explicit AX absence differs from unsupported, failed, or malformed focus."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class UserFocusStateTests(unittest.TestCase):
    def test_absence_is_comparable_but_unknown_focus_never_passes(self):
        source = (ROOT/'helper/WindowDispatch.swift').read_text()
        state = source[source.index('struct NativeUserInputState {'):source.index('struct NativeWindowDispatchFailure:')]
        parser = source[source.index('func nativeUserFocusedElement('):source.index('func readNativeUserInputState(')]
        harness = r'''
import Foundation
import AppKit
import ApplicationServices
enum NativeWindowInputError:Error {case unavailable(String)}
'''
        checks = r'''
let element=AXUIElementCreateApplication(1)
precondition(try nativeUserFocusedElement(.noValue,nil)==nil)
precondition(CFEqual(try nativeUserFocusedElement(.success,element)!,element))
var refusals=[String]()
for pair:(AXError,AnyObject?) in [(.attributeUnsupported,nil),(.cannotComplete,nil),
 (.success,nil),(.success,"PRIVATE" as NSString),(.noValue,element)] {
 do {_ = try nativeUserFocusedElement(pair.0,pair.1);fatalError("unknown focus accepted")}
 catch NativeWindowInputError.unavailable(let code) {refusals.append(code)}
}
let absent=NativeUserInputState(foreground:42,pointer:.zero,focused:nil)
let focused=NativeUserInputState(foreground:42,pointer:.zero,focused:element)
precondition(absent.matches(absent) && focused.matches(focused))
precondition(absent.differences(focused)==["focus"] && focused.differences(absent)==["focus"])
precondition(absent.differences(NativeUserInputState(foreground:43,pointer:CGPoint(x:1,y:1),focused:nil))==["foreground","pointer"])
print(String(data:try! JSONSerialization.data(withJSONObject:refusals),encoding:.utf8)!)
'''
        # Evaluate throwing reads before precondition's nonthrowing autoclosure.
        checks = checks.replace('precondition(try nativeUserFocusedElement(.noValue,nil)==nil)',
                                'let none=try nativeUserFocusedElement(.noValue,nil);precondition(none==nil)')
        checks = checks.replace('precondition(CFEqual(try nativeUserFocusedElement(.success,element)!,element))',
                                'let some=try nativeUserFocusedElement(.success,element);precondition(CFEqual(some!,element))')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.swift'
            path.write_text(harness+state+parser+checks)
            result = subprocess.run(['swift',str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout), ['user focus unsupported','user focus query failed',
                         'user focus malformed','user focus malformed','user focus query failed'])
        self.assertNotIn('PRIVATE',result.stdout)
