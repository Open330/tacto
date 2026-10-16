import Foundation
@main struct Checks {
 static func main() {
  var state = SetupState()
  func json(_ text:String)->Data { text.data(using:.utf8)! }
  assert(state.receive(json("{\"accessibility_trusted\":true,\"screen_capture_preflight\":false,\"bundle_id\":\"ai.callabo.nlc.helper\",\"pid\":123}"),expected:"ai.callabo.nlc.helper"))
  assert(state.accessibility && !state.screen && !state.ready)
  for text in ["{}", "{\"accessibility_trusted\":true,\"screen_capture_preflight\":true,\"bundle_id\":\"wrong\",\"pid\":123}", "{\"accessibility_trusted\":true,\"screen_capture_preflight\":true,\"bundle_id\":\"ai.callabo.nlc.helper\",\"pid\":0}", "{\"accessibility_trusted\":\"true\",\"screen_capture_preflight\":true,\"bundle_id\":\"ai.callabo.nlc.helper\",\"pid\":123}", "not json"] {
   assert(!state.receive(json(text),expected:"ai.callabo.nlc.helper"))
   assert(!state.accessibility && !state.screen)
  }
  for ax in [false, true] {
   for screen in [false, true] {
    let receipt = "{\"accessibility_trusted\":\(ax),\"screen_capture_preflight\":\(screen),\"bundle_id\":\"ai.callabo.nlc.helper\",\"pid\":123}"
    assert(state.receive(json(receipt),expected:"ai.callabo.nlc.helper"))
    assert(state.ready == (ax && screen))
   }
  }
  print("Permission state: 10 cases PASS; both grants required for readiness")

  // Asked in order; nothing left to ask once both are on.
  var order = SetupState()
  assert(order.nextNeeded == .control)
  order.accessibility = true; assert(order.nextNeeded == .screen)
  order.screen = true; assert(order.nextNeeded == nil)

  // A watch ends on the grant it waits for, or on its own after the limit.
  let start = Date(timeIntervalSince1970: 1000)
  let watch = PermissionWatch(permission: .screen, started: start)
  var waiting = SetupState(); waiting.accessibility = true
  assert(watch.shouldContinue(at: start.addingTimeInterval(5), state: waiting))
  assert(!watch.shouldContinue(at: start.addingTimeInterval(PermissionWatch.limit), state: waiting))
  waiting.screen = true
  assert(!watch.shouldContinue(at: start.addingTimeInterval(5), state: waiting))
  print("Permission watch: order, grant and time limit PASS")

  // The helper panel sits beside System Settings and never off screen.
  let screen = CGRect(x: 0, y: 0, width: 1440, height: 900)
  let panel = CGSize(width: 260, height: 300)
  let right = accessoryOrigin(settings: CGRect(x: 300, y: 200, width: 700, height: 600), panel: panel, screen: screen)
  assert(right.x == 1012 && right.y == 500)
  let left = accessoryOrigin(settings: CGRect(x: 700, y: 200, width: 700, height: 600), panel: panel, screen: screen)
  assert(left.x == 428)
  let inside = accessoryOrigin(settings: CGRect(x: 100, y: 0, width: 1300, height: 900), panel: panel, screen: screen)
  assert(inside.x == 1400 - 260 - 12 && inside.y == 600)
  for settings in [CGRect(x: -400, y: 850, width: 700, height: 600), CGRect(x: 1300, y: -500, width: 700, height: 600)] {
    let p = accessoryOrigin(settings: settings, panel: panel, screen: screen)
    assert(p.x >= 0 && p.x + panel.width <= 1440 && p.y >= 0 && p.y + panel.height <= 900)
  }
  let flipped = cocoaRect(fromWindowList: CGRect(x: 10, y: 100, width: 700, height: 600), primaryHeight: 900)
  assert(flipped == CGRect(x: 10, y: 200, width: 700, height: 600))
  print("Accessory placement: right, left, inside, clamped, flipped PASS")
 }
}
