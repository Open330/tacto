"""Hover: bounded payload, no buttons, restoring the cursor, yielding to the person."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from tacto.native_hover import hover_problem, main
ROOT = Path(__file__).resolve().parents[1]


class HoverPayloadTests(unittest.TestCase):
    def test_bounded_points_times_and_capture(self):
        action = {'name': 'hover', 'element_index': 0, 'to': {'x': 20, 'y': 40}, 'duration': 0.3, 'hold': 1}
        self.assertIsNone(hover_problem(action))
        self.assertIsNone(hover_problem({**action, 'via': [{'x': 1, 'y': 2}] * 12, 'capture': True}))
        for changes in ({'duration': True}, {'hold': float('nan')}, {'hold': 3}, {'duration': -1},
                        {'to': {'x': True, 'y': 2}}, {'to': {'x': -1, 'y': 2}},
                        {'via': [{'x': 1, 'y': 2}] * 13}, {'capture': 1},
                        {'duration': 2, 'hold': 2, 'capture': True}, {'button': 'left'}):
            self.assertIsNotNone(hover_problem({**action, **changes}), changes)
        self.assertIsNotNone(hover_problem({'name': 'hover', 'to': {'x': 1, 'y': 1}}))


class FakeDriver:
    capabilities = ('action.hover', 'hover.pointer_restored')

    def __init__(self, reply):
        self.reply, self.acts = reply, []

    def observe(self, target, mode):
        return {'type': 'observation', 'obs_id': 'o1', 'tree': '0 Window "Pet"\n1 Image "character"'}

    def act(self, obs_id, target, action, **kwargs):
        self.acts.append((action, kwargs))
        return self.reply


class HoverCommandTests(unittest.TestCase):
    def run_main(self, argv, driver):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(argv, driver)
        return code, json.loads(out.getvalue())

    def test_sends_one_authorized_hover_and_saves_the_hold_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            helper_png = Path(folder) / 'helper.png'
            helper_png.write_bytes(b'\x89PNG fake')
            reply = {'type': 'acted', 'action': 'hover', 'pointer_restored': True, 'buttons': 0,
                     'window_frames': [{'x': 0, 'y': 0, 'width': 100, 'height': 80}],
                     'hold_capture': {'type': 'capture', 'path': str(helper_png), 'width': 200, 'height': 160,
                                      'window_frame': [0, 0, 100, 80]}}
            driver = FakeDriver(reply)
            output = Path(folder) / 'during.png'
            code, result = self.run_main(['com.example.pet', '--helper', '--to', '30', '40', '--via', '10', '10',
                                          '--hold', '0.5', '--capture', str(output)], driver)
            self.assertEqual(0, code)
            action, kwargs = driver.acts[0]
            self.assertEqual({'name': 'hover', 'element_index': 0, 'to': {'x': 30, 'y': 40},
                              'via': [{'x': 10, 'y': 10}], 'duration': 0.3, 'hold': 0.5, 'capture': True}, action)
            self.assertEqual(('preapproved', True), (kwargs['risk_class'], kwargs['approved']))
            self.assertEqual(b'\x89PNG fake', output.read_bytes())
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertEqual({'x': 0.5, 'y': 0.5}, result['capture']['pixel_to_point'])
            self.assertNotIn('hold_capture', result)
            self.assertTrue(result['pointer_restored'])

    def test_refuses_existing_capture_path_and_old_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = Path(folder) / 'x.png'; existing.write_bytes(b'mine')
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                    unittest.mock.patch('sys.stderr', io.StringIO()):
                main(['com.example.pet', '--helper', '--to', '1', '1', '--capture', str(existing)], FakeDriver({}))
            self.assertEqual(b'mine', existing.read_bytes())
        old = FakeDriver({}); old.capabilities = ()
        code, result = self.run_main(['com.example.pet', '--helper', '--to', '1', '1'], old)
        self.assertEqual((1, 'hover_unavailable', 'not_started'), (code, result['error'], result['effect']))
        self.assertEqual([], old.acts)


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class NativeHoverTests(unittest.TestCase):
    def test_path_restore_person_takeover_and_refusals(self):
        # Fake IO: nothing here moves the real cursor.
        harness = r'''
import Foundation
import AppKit
var cursor = CGPoint(x: 500, y: 500)
var moves: [CGPoint] = []
var clock = 0.0
var frameValue = CGRect(x: 100, y: 200, width: 300, height: 200)
var takeOverAt: Int? = nil
func io() -> PointerHoverIO {
    PointerHoverIO(location: {
            if let n = takeOverAt, moves.count >= n { cursor = CGPoint(x: 900, y: 900) }
            return cursor },
        move: { cursor = $0; moves.append($0); return true },
        frame: { frameValue },
        owner: { _ in 42 },
        now: { clock },
        sleep: { clock += $0; if clock > 0.5 { frameValue.origin.x = 160 } })
}
func run(_ path: [CGPoint], duration: Double = 0.1, hold: Double = 1, deadline: Double = 5,
         stopped: Bool = false, capture: Bool = false) -> [String: Any] {
    moves = []; clock = 0; cursor = CGPoint(x: 500, y: 500); frameValue.origin.x = 100
    return dispatchPointerHover(pid: 42, path: path, duration: duration, hold: hold, deadline: deadline,
        stopped: { stopped }, context: {}, io: io(),
        atHoldEnd: capture ? { ["type": "capture", "cursor_x": Double(cursor.x)] } : nil)
}
var r = run([CGPoint(x: 10, y: 10), CGPoint(x: 50, y: 10)], capture: true)
precondition(r["type"] as? String == "acted", "\(r)")
precondition(r["buttons"] as? Int == 0 && r["pointer_restored"] as? Bool == true)
precondition(moves.first!.x >= 110 && moves.first!.y == 210, "\(moves)")
precondition(moves[moves.count - 2] == CGPoint(x: 150, y: 210), "\(moves)")
precondition(moves.last == CGPoint(x: 500, y: 500) && cursor == CGPoint(x: 500, y: 500))
precondition(r["window_moved"] as? Bool == true && (r["window_frames"] as! [[String: Double]]).count == 2)
precondition(((r["hold_capture"] as! [String: Any])["cursor_x"] as! Double) == 150, "captured after restore")
precondition(r["hit_owner_matches"] as? [Bool] == [true, true])
// The person moves the mouse: stop, and leave their cursor where they put it.
takeOverAt = 3
r = run([CGPoint(x: 10, y: 10)], duration: 0.2)
precondition(r["error"] as? String == "person_moved_pointer" && r["effect"] as? String == "unknown", "\(r)")
precondition(r["pointer_restored"] as? Bool == false && cursor == CGPoint(x: 900, y: 900))
takeOverAt = nil
// A move that fails after shifting the cursor is reported, never restored on a guess.
var failingMove = false, locationGone = false
func edgeIO() -> PointerHoverIO {
    PointerHoverIO(location: { locationGone && !moves.isEmpty ? nil : cursor },
        move: { cursor = $0; moves.append($0); return !failingMove },
        frame: { frameValue }, owner: { _ in 42 }, now: { clock }, sleep: { clock += $0 })
}
func runEdge() -> [String: Any] {
    moves = []; clock = 0; cursor = CGPoint(x: 500, y: 500)
    return dispatchPointerHover(pid: 42, path: [CGPoint(x: 10, y: 10)], duration: 0.1, hold: 1, deadline: 5,
        stopped: { false }, context: {}, io: edgeIO())
}
failingMove = true
r = runEdge()
precondition(r["effect"] as? String == "unknown" && r["pointer_restored"] as? Bool == false, "\(r)")
precondition(moves.count == 1, "no further warp after a failed move: \(moves)")
failingMove = false; locationGone = true
r = runEdge()
precondition(r["error"] as? String == "pointer_location_unavailable" && r["effect"] as? String == "unknown", "\(r)")
precondition(moves.count == 1 && r["pointer_restored"] as? Bool == false, "no restore on a guess: \(moves)")
locationGone = false
// The person moving between reading the start and the first move.
var nudgeBeforeFirst = true
moves = []; clock = 0
r = dispatchPointerHover(pid: 42, path: [CGPoint(x: 10, y: 10)], duration: 0.1, hold: 1, deadline: 5,
    stopped: { false }, context: {}, io: PointerHoverIO(
        location: { if nudgeBeforeFirst && moves.isEmpty && clock == 0 { nudgeBeforeFirst = false; return CGPoint(x: 500, y: 500) }; return moves.isEmpty ? CGPoint(x: 900, y: 900) : cursor },
        move: { cursor = $0; moves.append($0); return true },
        frame: { frameValue }, owner: { _ in 42 }, now: { clock }, sleep: { clock += $0 }))
precondition(r["error"] as? String == "person_moved_pointer" && r["effect"] as? String == "not_started", "\(r)")
// Refused before any movement.
for (path, duration, hold, deadline, stopped) in [
        ([CGPoint(x: 300, y: 10)], 0.1, 1.0, 5.0, false),      // outside the window
        ([CGPoint(x: 10, y: 10)], 2.0, 2.0, 3.0, false),       // deadline too short
        ([CGPoint(x: 10, y: 10)], 0.1, 3.0, 9.0, false),       // hold too long
        ([CGPoint(x: 10, y: 10)], 0.1, 1.0, 5.0, true)] {      // Esc
    r = run(path, duration: duration, hold: hold, deadline: deadline, stopped: stopped)
    precondition(r["type"] as? String == "error" && r["effect"] as? String == "not_started", "\(r)")
    precondition(moves.isEmpty && cursor == CGPoint(x: 500, y: 500))
}
print("HOVER_PASS")
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'main.swift'; binary = Path(directory) / 'check'
            source.write_text(harness)
            build = subprocess.run(['xcrun', 'swiftc', str(source), str(ROOT / 'helper/PointerHover.swift'),
                                    str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                                    str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(0, build.returncode, build.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('HOVER_PASS', result.stdout.strip())

    def test_launcher_and_helper_gate_hover_like_other_coordinates(self):
        launcher = (ROOT / 'launcher/tacto.swift').read_text()
        helper = (ROOT / 'helper/TactoHelper.swift').read_text()
        self.assertIn('"hover":            (["name", "element_index", "to", "duration", "hold"], ["via", "capture"])', launcher)
        for source in (launcher, helper):
            self.assertIn('["click_point", "drag", "hover", "vroid_drag", "vroid_focus"]', source)
        self.assertIn('"action.hover", "hover.pointer_restored"', helper)
        hover = helper[helper.index('    case "hover":'):helper.index('    case "click_point":')]
        self.assertNotIn('leftMouse', hover + (ROOT / 'helper/PointerHover.swift').read_text())
        self.assertNotIn('activate', hover)


if __name__ == '__main__':
    unittest.main()
