"""Whatever touches the screen shows that it is happening.

Found 2026-09-20 while verifying browsers: the outline was wired at two CLI
commands and two signed-helper messages, so every other way in — a script
holding a Driver, `tacto observe`, the whole evaluation suite, this session's own
browser and Calculator probes — drove somebody's machine with nothing on screen
to say so. Asking each caller to remember is how it went missing; the driver
owns it now.
"""
import importlib.util
import os
import pathlib
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class ControlIndicatorTests(unittest.TestCase):
    def setUp(self):
        from tacto import driver
        self.m = driver
        self.d = object.__new__(driver.Driver)
        self.d._indicate = True
        self.d._indicator = None
        self.d._indicator_pid = None
        self.d._indicator_beat = None
        self.started = []

    def fake_indicator(self, alive=True):
        started = self.started
        class Fake:
            def __init__(self, pid, heartbeat=None, ttl=4.0, **kw):
                self.pid, self.heartbeat, self.ttl = pid, heartbeat, ttl
                self._alive = alive
            def __enter__(self):
                started.append(self)
                return self
            def __exit__(self, *exc):
                self._alive = False
                return False
            def alive(self):
                return self._alive
        return Fake

    def show(self, pid, **kw):
        import tacto.overlay
        with patch.object(tacto.overlay, 'Indicator', self.fake_indicator(**kw)):
            self.d._showing_control(pid)

    def test_touching_an_app_puts_the_outline_on_it(self):
        self.show(4321)
        self.assertEqual(1, len(self.started))
        self.assertEqual(4321, self.started[0].pid)
        self.assertTrue(self.d.indicator()['showing'])
        self.assertEqual(4321, self.d.indicator()['pid'])

    def test_the_same_app_again_does_not_start_a_second_one(self):
        self.show(4321)
        self.show(4321)
        self.assertEqual(1, len(self.started), 'one outline, kept alive')

    def test_a_different_app_moves_the_outline(self):
        self.show(4321)
        self.show(9876)
        self.assertEqual(2, len(self.started))
        self.assertFalse(self.started[0].alive(), 'the first is taken down')
        self.assertEqual(9876, self.d.indicator()['pid'])

    def test_the_heartbeat_is_touched_every_time(self):
        self.show(4321)
        beat = self.d._indicator_beat
        self.assertTrue(os.path.exists(beat))
        os.utime(beat, (0, 0))
        self.show(4321)
        self.assertGreater(os.stat(beat).st_mtime, 1,
                           'a driver still working keeps the outline alive')

    def test_a_dead_child_is_replaced_rather_than_assumed(self):
        self.show(4321, alive=False)
        self.show(4321, alive=False)
        self.assertEqual(2, len(self.started))

    def test_failing_to_draw_never_takes_down_the_task(self):
        import tacto.overlay
        def explode(*a, **kw):
            raise RuntimeError('no window server')
        with patch.object(tacto.overlay, 'Indicator', explode):
            self.d._showing_control(4321)          # must not raise
        self.assertFalse(self.d.indicator()['showing'], 'and it says so')

    def test_nothing_is_drawn_for_a_target_that_is_not_running(self):
        for pid in (None, 0, -1, True, '4321'):
            with self.subTest(pid=pid):
                self.show(pid)
        self.assertEqual([], self.started)

    def test_the_opt_out_is_explicit(self):
        self.d._indicate = False
        self.show(4321)
        self.assertEqual([], self.started)
        self.assertFalse(self.d.indicator()['enabled'])

    def test_closing_takes_the_outline_down_and_cleans_up(self):
        self.show(4321)
        beat = self.d._indicator_beat
        self.d.close()
        self.assertFalse(self.started[0].alive())
        self.assertFalse(os.path.exists(beat))
        self.assertIsNone(self.d.indicator()['pid'])

    def test_observe_and_act_both_show_it(self):
        """The two places that reach another app, checked at the surface."""
        import inspect
        for name in ('observe', 'act'):
            source = inspect.getsource(getattr(self.m.Driver, name))
            self.assertIn('_showing_control(', source,
                          f'{name} must say that it is happening')


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class IndicatorLifetimeTests(unittest.TestCase):
    """The outline leaves on its own, because one that never does stops being read."""

    def test_the_child_is_told_the_heartbeat_and_the_deadline(self):
        from tacto.overlay import Indicator
        seen = {}
        class Popen:
            pid = 999999
            def __init__(self, command, **kw):
                seen['command'] = command
            def poll(self):
                return None
            def terminate(self):
                pass
        with patch('subprocess.Popen', Popen):
            with Indicator(4321, heartbeat='/tmp/beat', ttl=2.5):
                pass
        self.assertIn('/tmp/beat', seen['command'])
        self.assertIn('2.5', seen['command'])
        self.assertIn('4321', seen['command'])

    def test_without_a_heartbeat_the_child_is_not_given_one(self):
        from tacto.overlay import Indicator
        seen = {}
        class Popen:
            pid = 999999
            def __init__(self, command, **kw):
                seen['command'] = command
            def poll(self):
                return None
            def terminate(self):
                pass
        with patch('subprocess.Popen', Popen):
            with Indicator(4321):
                pass
        self.assertEqual(['4321'], [c for c in seen['command'] if c == '4321'])
        self.assertEqual(['-I', '-B', '-m', 'tacto.overlay', '4321'], seen['command'][1:])


ROOT = pathlib.Path(__file__).resolve().parent.parent


class TheIndicatorCoversNothing(unittest.TestCase):
    """It was an outline drawn around the target window. It is a menu-bar item.

    The outline was wrong twice over. It put our own drawing on top of
    someone's screen, and it carried `canJoinAllSpaces` — "make sure it is
    visible", which in practice meant it followed the user to every desktop and
    drew a box around empty space over unrelated work, while the window it
    described was not on that screen at all (user report, 2026-09-21).

    A menu-bar item hides nothing and follows nobody. These tests exist because
    the obvious "fix" is to bring the panel back with one flag removed, and the
    panel itself was the problem.
    """

    def test_the_helper_reuses_the_apps_persistent_menu(self):
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        self.assertNotIn("NSStatusBar.system.statusItem(withLength:", text)
        self.assertIn("RuntimePublisher.shared.start(pid:pid)", text)
        self.assertIn("NSStatusBar.system.statusItem(withLength:", (ROOT/"setup/main.swift").read_text())
        self.assertIn("func showControlIndicator(forPID", text)

    def test_the_python_indicator_shows_a_menu_bar_item(self):
        text = (ROOT / "tacto/overlay.py").read_text()
        self.assertIn("statusItemWithLength_", text)

    def test_control_never_becomes_key_or_follows_other_spaces(self):
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        body=text[text.index('final class TactoControlPanel'):text.index('/// Supplementary menu-bar status')]
        self.assertIn('override var canBecomeKey: Bool { false }',body)
        self.assertIn('override var canBecomeMain: Bool { false }',body)
        self.assertIn('.nonactivatingPanel',body)
        self.assertIn('becomesKeyOnlyIfNeeded = true',body)
        self.assertNotIn('canJoinAllSpaces',body)
        self.assertNotIn('moveToActiveSpace',body)
        self.assertIn('panel.isOnActiveSpace',body)
        self.assertIn('avoiding: targets',body)

    def test_placement_keeps_control_outside_target_window(self):
        import json,shutil,subprocess,tempfile
        if not shutil.which('swift'):self.skipTest('macOS Swift required')
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        code=text[text.index('func availableControlFrame('):text.index('func controlTargetFrames(')]
        harness='import Foundation\nimport CoreGraphics\n'+code+r'''
let screen=CGRect(x:0,y:0,width:1000,height:800)
let size=CGSize(width:292,height:56)
let first=availableControlFrame(screens:[screen],avoiding:[],size:size)!
let moved=availableControlFrame(screens:[screen],avoiding:[first],size:size)!
let blocked=availableControlFrame(screens:[screen],avoiding:[screen],size:size)
let other=CGRect(x:1000,y:0,width:1000,height:800)
let second=availableControlFrame(screens:[screen,other],avoiding:[screen],size:size)!
print(String(data:try! JSONSerialization.data(withJSONObject:["inside":screen.contains(first),"moved":!first.intersects(moved),"blocked":blocked == nil,"other":other.contains(second)]),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'main.swift';path.write_text(harness)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual({'inside':True,'moved':True,'blocked':True,'other':True},json.loads(result.stdout))

    def test_compact_control_fits_narrow_gutter_without_covering_target(self):
        import json, shutil, subprocess, tempfile
        if not shutil.which('swift'): self.skipTest('macOS Swift required')
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        code=text[text.index('func availableControlFrame('):text.index('func controlTargetFrames(')]
        harness='import Foundation\nimport CoreGraphics\n'+code+r'''
let screen=CGRect(x:0,y:62,width:1512,height:887)
let targets=[CGRect(x:72,y:62,width:1324,height:887),CGRect(x:410,y:62,width:1015,height:754)]
let compact=availableControlPanelFrame(screens:[screen],avoiding:targets)!
let normal=availableControlPanelFrame(screens:[screen],avoiding:[])!
let blocked=availableControlPanelFrame(screens:[screen],avoiding:[screen])
print(String(data:try! JSONSerialization.data(withJSONObject:["compact":compact.width==96,"inside":screen.contains(compact),"outside":!targets.contains(where:{$0.intersects(compact)}),"normal":normal.width==292,"blocked":blocked==nil]),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'main.swift';path.write_text(harness)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual(dict(compact=True,inside=True,outside=True,normal=True,blocked=True),json.loads(result.stdout))

    def test_only_a_panel_with_no_room_falls_back_to_the_menu(self):
        """A maximized target leaves no corner for the panel; the panel is
        optional, so the menu and Esc carry on. A panel that exists but cannot
        be seen still refuses."""
        import json, shutil, subprocess, tempfile
        if not shutil.which('swift'): self.skipTest('macOS Swift required')
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        code=text[text.index('func controlPanelHasNoRoom('):text.index('/// How the last accepted action was shown')]
        harness='import Foundation\n'+code+r'''
let cases:[[String:Bool]]=[["frame_available":false],["target_frames_available":false],
    ["created":true,"visible":false,"active_space":true],[:]]
print(String(data:try! JSONSerialization.data(withJSONObject:cases.map{controlPanelHasNoRoom($0)}),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'main.swift';path.write_text(harness)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual([True,False,False,False],json.loads(result.stdout))
        body=text[text.index('func ensureVisibleControl('):]
        body=body[:body.index('\n}\n')]
        fallback=body[body.index('func proceedWithoutPanel()'):]
        fallback=fallback[:fallback.index('\n    }\n')]
        # Hiding the panel never skips the session, Esc or menu checks.
        self.assertIn('controlPanel?.orderOut(nil)',fallback)
        self.assertIn('!abortState.isStopped && sessionVisible()',fallback)
        self.assertNotIn('activate',fallback)
        self.assertEqual(2,body.count('controlPanelHasNoRoom(controlPanelDiagnostic)'))

    def test_an_earlier_no_room_reason_cannot_excuse_a_panel_on_another_space(self):
        """Codex review of 6151abd: no room once, then room but the existing
        panel is on another Space. The second refusal must say so."""
        import shutil, subprocess, tempfile
        if not shutil.which('swift'): self.skipTest('macOS Swift required')
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        head=text[text.index('func showControlPanel('):text.index('    if controlPanel == nil {',text.index('func showControlPanel('))]
        rule=text[text.index('func controlPanelHasNoRoom('):text.index('/// How the last accepted action was shown')]
        harness=r'''import Foundation
import CoreGraphics
struct FakeScreen { var visibleFrame=CGRect(x:0,y:0,width:1200,height:800) }
enum NSScreen { static let screens=[FakeScreen()] }
struct NSRunningApplication { init?(processIdentifier:Int32) { return nil }; var localizedName:String? { nil } }
final class Panel { var isOnActiveSpace=false; func setFrame(_ rect:CGRect,display:Bool) {} }
var controlPanel:Panel? = Panel()
var controlPanelDiagnostic:[String:Bool]=[:]
var frameAvailable=false
func availableControlPanelFrame(screens:[CGRect],avoiding:[CGRect])->CGRect? {
    frameAvailable ? CGRect(x:1,y:1,width:292,height:56) : nil
}
'''+head+"    _ = who; _ = size\n    return true\n}\n"+rule+r'''
let first=showControlPanel(forPID:1,targets:[])
let firstNoRoom=controlPanelHasNoRoom(controlPanelDiagnostic)
frameAvailable=true
let second=showControlPanel(forPID:1,targets:[])
print(first, firstNoRoom, second, controlPanelHasNoRoom(controlPanelDiagnostic), controlPanelDiagnostic)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'main.swift';path.write_text(harness)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual('false true false false ["active_space": false]',result.stdout.strip())

    def test_the_indicator_still_gates_every_action(self):
        # The point of it is that a caller cannot act invisibly. Losing the
        # outline must not lose that.
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        self.assertGreaterEqual(text.count("ensureVisibleControl(forPID: p"), 4)
        self.assertIn("indicator_unavailable", text)

    def test_ax_control_does_not_start_screen_capture(self):
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        body=text[text.index('func ensureVisibleControl('):]
        body=body[:body.index('\n}\n')]
        self.assertIn('showControlPanel(forPID: pid, targets:targets)',body)
        self.assertIn('guard shown else',body)
        self.assertNotIn('ensureControlStream',body)
        self.assertNotIn('SCStream',body)

    def test_windowless_judgement_runs(self):
        """The real function against a fake read (june-mba, 2026-10-09: Finder
        with no window lists only its desktop AXScrollArea)."""
        import json, shutil, subprocess, tempfile
        if not shutil.which("xcrun"):
            self.skipTest("macOS Swift required")
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        code = text[text.index("/// True only when AXWindows was read successfully and is empty — or, for"):
                    text.index("func appSaysItHasNoWindows(")]
        harness = r'''
import Foundation
import ApplicationServices
typealias AXRead = (AXUIElement, String) throws -> (AXError, CFTypeRef?)
''' + code + r'''
func el(_ n: Int32) -> AXUIElement { AXUIElementCreateApplication(n) }
func pidOf(_ e: AXUIElement) -> Int32 { var p: pid_t = 0; AXUIElementGetPid(e, &p); return p }
struct Timeout: Error {}
func judge(_ windows: Any?, _ roles: [Int32: Any], finder: Bool = true, parents: [Int32: Int32] = [:]) -> Bool {
    noWindowsAmong(el(1), isFinder: finder, read: { e, name in
        let n = pidOf(e)
        if n == 1 {
            if let err = windows as? AXError { return (err, nil) }
            if let list = windows as? [Int32] { return (.success, list.map { el($0) } as CFArray) }
            return (.success, "x" as CFString)
        }
        if name == "AXParent" {
            guard let p = parents[n] ?? (roles[n] != nil ? 1 : nil) else { return (.cannotComplete, nil) }
            return (.success, el(p))
        }
        guard let r = roles[n] else { return (.noValue, nil) }
        if let err = r as? AXError { return (err, nil) }
        if let s = r as? String, s == "throw" { throw Timeout() }
        return (.success, r as! String as CFString)
    })
}
let out: [String: Bool] = [
    "empty": judge([Int32](), [:]),
    "empty, not Finder": judge([Int32](), [:], finder: false),
    "Finder desktop": judge([Int32(2)], [2: "AXScrollArea"]),
    "scroll area in another app": judge([Int32(2)], [2: "AXScrollArea"], finder: false),
    "scroll area with another parent": judge([Int32(2)], [2: "AXScrollArea"], parents: [2: 9]),
    "a dialog": judge([Int32(2)], [2: "AXDialog"]),
    "an unknown role": judge([Int32(2)], [2: "AXUnknown"]),
    "a window": judge([Int32(2), Int32(3)], [2: "AXScrollArea", 3: "AXWindow"]),
    "a sheet": judge([Int32(2)], [2: "AXSheet"]),
    "role unreadable": judge([Int32(2)], [2: AXError.cannotComplete]),
    "role missing": judge([Int32(2)], [:]),
    "role read times out": judge([Int32(2)], [2: "throw"]),
    "list unreadable": judge(AXError.cannotComplete, [:]),
    "list malformed": judge("x", [:]),
]
print(String(data: try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]), encoding: .utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix="tacto-windowless-") as directory:
            source = pathlib.Path(directory) / "main.swift"
            source.write_text(harness)
            result = subprocess.run(["swift", str(source)], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({"empty": True, "empty, not Finder": True, "Finder desktop": True,
                          "scroll area in another app": False, "scroll area with another parent": False,
                          "a dialog": False, "an unknown role": False, "a window": False, "a sheet": False,
                          "role unreadable": False, "role missing": False, "role read times out": False,
                          "list unreadable": False, "list malformed": False}, json.loads(result.stdout))

    def test_only_menu_requests_may_use_the_windowless_gate(self):
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        calls = [line.strip() for line in text.splitlines()
                 if "ensureVisibleControl(forPID: p" in line]
        self.assertEqual(sorted(calls), sorted([
            "guard ensureVisibleControl(forPID: p, windowless: true) else {",
            'guard ensureVisibleControl(forPID: p, windowless: ["menu", "open_folder", "reopen_window", "close_menu"].contains((action["name"] as? String) ?? "")) else {',
            "guard ensureVisibleControl(forPID: p) else {",
            'guard ensureVisibleControl(forPID: p, windowless: kind == "menu_state" || kind == "menu_items") else {',
        ]))

    def test_the_held_stream_sees_one_window_not_the_screen(self):
        # `SCContentFilter(display:)` stood here and captured everything, so an
        # indicator meaning "TACTO is looking" was looking at every other window
        # the person had open (user report, 2026-09-21). The indicator may not
        # cost more than what it announces.
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        body = text[text.index("func ensureControlStream(forPID"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("SCContentFilter(desktopIndependentWindow:", body)
        # In code, not in the comment that explains why it is gone.
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            self.assertNotIn("SCContentFilter(display:", stripped)

    def test_the_stream_follows_the_window_it_is_about(self):
        # A run that moves to another window must move the indicator with it,
        # or it reports capture of something we stopped touching.
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        self.assertIn("controlStreamWindow", text)
        body = text[text.index("func ensureControlStream(forPID"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("stopCapture", body)

    def test_the_refusal_names_the_permission_it_needs(self):
        # "조작 표시를 띄울 수 없습니다" told nobody what to do about it, and the
        # cause is now one specific switch.
        # Since 2026-10-09 the refusal says WHICH gate refused: the Screen
        # Recording advice is given only when the stream could not start; a
        # menu-bar item that is not on screen says that instead (june-mba).
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        body = text[text.index("func indicatorRefusal("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("화면 기록 에서", body)
        self.assertIn("'\\(permissionHolderName)' 가 켜져 있는지", body)
        self.assertIn('case "menu_item_not_on_screen":', body)
        self.assertIn('"reason": reason', body)
        self.assertEqual(3, text.count("return indicatorRefusal("))

    def test_the_held_stream_keeps_nothing(self):
        # It is lit by really capturing, so it takes the least it can: the
        # frames are dropped and the stream is small and slow. A sink that
        # started keeping frames would be taking more of someone's screen than
        # the indicator promises.
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        sink = text[text.index("final class DiscardingSink"):]
        sink = sink[:sink.index("\n}\n")]
        self.assertNotIn("append", sink)
        self.assertNotIn("=", sink.split("of type: SCStreamOutputType)")[-1])

    def test_it_names_the_app_being_driven(self):
        # "something is happening" is not the same promise as "this app is
        # being driven", and the menu bar has room to say which.
        swift = (ROOT / "helper/TactoHelper.swift").read_text()
        python = (ROOT / "tacto/overlay.py").read_text()
        self.assertIn("localizedName", swift)
        self.assertIn("localizedName", python)


class TheIndicatorCanBeActedOn(unittest.TestCase):
    """An indicator you cannot act on is half of what it promises.

    It said "TACTO is working on Calculator · Esc" and that was the whole of it:
    someone who did not know about Esc was told something was happening and
    given nothing to do about it. Codex Computer Use's own status item carries
    a menu (`ComputerUseIPCCodexStatusItemMenuState`); macOS has no system
    indicator for accessibility control to inherit one from — Control Center's
    sensor indicators cover camera, microphone and screen capture, and nothing
    else — so this is ours to build.
    """

    def test_the_indicator_has_a_menu(self):
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        self.assertIn("func controlMenu(for appName: String?) -> NSMenu", text)
        self.assertIn("#selector(stopRuntime)", (ROOT/"setup/main.swift").read_text())

    def test_the_menu_names_what_is_being_driven(self):
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        body = text[text.index("func controlMenu(for appName"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("appName.map", body)

    def test_stopping_goes_through_the_one_abort_everything_consults(self):
        # Not a second flag. Every action already asks `abortState`, and a
        # menu that set its own would be a second answer to one question.
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        body = text[text.index("@objc func stopNow(_ sender: Any?)"):]
        body = body[:body.index("\n    }\n")]
        self.assertIn("abortState.stopNow()", body)

    def test_the_menu_stop_is_not_routed_through_the_staleness_check(self):
        # `note(at:)` exists because an Esc can reach the run loop after the
        # run it was aimed at ended. A click on an indicator that only exists
        # during this run cannot be stale, and manufacturing a timestamp to
        # pass that test would be asking a question whose answer is known.
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        self.assertIn("func stopNow() {", text)
        body = text[text.index("@objc func stopNow(_ sender: Any?)"):]
        body = body[:body.index("\n    }\n")]
        self.assertNotIn("note(at:", body)

    def test_the_menu_target_cannot_be_read_before_it_exists(self):
        # `showControlIndicator` is reachable from diagnostic flags that exit
        # before a global declared further down would have been initialised —
        # the trap that silently ate the logger's output earlier today. A
        # type's static is created on first use.
        text = (ROOT / "helper/TactoHelper.swift").read_text()
        self.assertIn("static let shared = ControlMenuTarget()", text)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            self.assertNotIn("let controlMenuTarget =", stripped,
                             "a top-level global here depends on source order")
