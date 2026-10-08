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
 }
}
