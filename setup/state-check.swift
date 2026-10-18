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
  assert(state.ready)
  assert(!state.refresh(nil, expected: "ai.callabo.nlc.helper"))
  assert(!state.ready && !state.accessibility && !state.screen)
  state.accessibility = true; state.screen = true
  assert(!state.refresh(json("{}"), expected: "ai.callabo.nlc.helper"))
  assert(!state.ready)
  print("Permission state: 12 cases PASS; both grants required for readiness")

  let helper = URL(fileURLWithPath: "/checkout/helper/Tacto Helper.app")
  let setup = URL(fileURLWithPath: "/Applications/Tacto.app")
  assert(permissionApp(.control, helper: helper, setupApp: setup, bundledRuntime: true) == helper)
  assert(permissionApp(.screen, helper: helper, setupApp: setup, bundledRuntime: true) == setup)
  assert(permissionApp(.control, helper: helper, setupApp: setup, bundledRuntime: false) == helper)
  assert(permissionApp(.screen, helper: helper, setupApp: setup, bundledRuntime: false) == helper)
  print("Permission owner: standalone screen uses outer app; AX and checkout use helper PASS")

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

  // Settings may not exist yet; a Touch ID/password dialog is never an anchor.
  let mainWindow = SettingsWindowCandidate(id: 10, bounds: CGRect(x: 100, y: 100, width: 700, height: 600))
  let password = SettingsWindowCandidate(id: 11, bounds: CGRect(x: 300, y: 200, width: 440, height: 260))
  assert(settingsMainWindow([], anchoredID: nil) == nil)
  assert(settingsMainWindow([password], anchoredID: nil) == nil)
  assert(settingsMainWindow([password, mainWindow], anchoredID: nil)?.id == 10)
  let largeModal = SettingsWindowCandidate(id: 12, bounds: CGRect(x: 0, y: 0, width: 800, height: 650))
  assert(settingsMainWindow([largeModal, mainWindow], anchoredID: 10)?.id == 10)
  assert(settingsMainWindow([password, largeModal], anchoredID: 10) == nil)
  let moved = SettingsWindowCandidate(id: 10, bounds: CGRect(x: 500, y: 300, width: 700, height: 600))
  assert(settingsMainWindow([password, moved], anchoredID: 10)?.bounds == moved.bounds)
  let reopened = SettingsWindowCandidate(id: 20, bounds: mainWindow.bounds)
  assert(settingsMainWindow([reopened], anchoredID: 10)?.id == 20)
  assert(settingsMainWindow([password, reopened], anchoredID: 10) == nil)
  print("Settings anchor: delayed window, password-only, modal front, lost anchor and movement PASS")

  // Real isolated filesystem layouts: readiness must follow the installed
  // links, including another version, partial installs and foreign files.
  let fm = FileManager.default
  let temp = fm.temporaryDirectory.appendingPathComponent("tacto-connection-check-" + UUID().uuidString)
  defer { try? fm.removeItem(at: temp) }
  let root = temp.appendingPathComponent("runtime"), home = temp.appendingPathComponent("home")
  func directory(_ path: URL) { try! fm.createDirectory(at: path, withIntermediateDirectories: true) }
  directory(root.appendingPathComponent("launcher")); directory(root.appendingPathComponent("skill"))
  try! Data("fixture".utf8).write(to: root.appendingPathComponent("launcher/tacto"))
  try! Data("---\nname: tacto\n---\n".utf8).write(to: root.appendingPathComponent("skill/SKILL.md"))
  func status(_ environment: [String:String] = [:]) -> AgentConnection { agentConnection(root: root, home: home, environment: environment) }
  assert(status() == .missing)
  let targets = [(".local/bin/tacto", "launcher/tacto"), (".local/bin/nlc", "launcher/tacto"),
                 (".claude/skills/tacto", "skill"), (".codex/skills/tacto", "skill")]
  for (name, destination) in targets {
    let link = home.appendingPathComponent(name); directory(link.deletingLastPathComponent())
    try! fm.createSymbolicLink(at: link, withDestinationURL: root.appendingPathComponent(destination))
  }
  assert(status() == .current)
  let canonicalRoot = URL(fileURLWithPath: root.path, isDirectory: true)
  let canonicalHome = URL(fileURLWithPath: home.path, isDirectory: true)
  let repeatedProfile = ["CODEX_HOME": home.appendingPathComponent(".codex").path + "/"]
  assert(agentConnection(root: canonicalRoot, home: canonicalHome, environment: repeatedProfile) == .current)
  assert(agentConnectionLinks(root: canonicalRoot, home: canonicalHome, environment: repeatedProfile).count == 4)
  assert(status(["TACTO_BIN_DIR":"", "NLC_BIN_DIR":""]) == .current)
  let codex = home.appendingPathComponent(".codex/skills/tacto")
  try! fm.removeItem(at: codex); assert(status() == .missing)
  let previous = temp.appendingPathComponent("previous/skill"); directory(previous)
  try! Data("---\nname: tacto\n---\n".utf8).write(to: previous.appendingPathComponent("SKILL.md"))
  try! fm.createSymbolicLink(at: codex, withDestinationURL: previous); assert(status() == .other)
  try! fm.removeItem(at: codex); directory(codex)
  try! Data("foreign skill".utf8).write(to: codex.appendingPathComponent("SKILL.md")); assert(status() == .conflict)
  try! fm.removeItem(at: codex); try! fm.createSymbolicLink(at: codex, withDestinationURL: root.appendingPathComponent("skill"))
  assert(status(["CODEX_HOME":temp.appendingPathComponent("profile").path]) == .missing)
  let timings = (0..<50).map { _ -> Double in
    let began = ProcessInfo.processInfo.systemUptime
    assert(status() == .current)
    return (ProcessInfo.processInfo.systemUptime - began) * 1000
  }.sorted()
  print(String(format: "Connection readback: 50 local samples, median %.3f ms, max %.3f ms; no process launch", timings[25], timings[49]))
  let command = home.appendingPathComponent(".local/bin/tacto")
  let previousRoot = previous.deletingLastPathComponent()
  directory(previousRoot.appendingPathComponent(".venv/bin"))
  for name in ["nlc", "tacto"] {
    let entry = previousRoot.appendingPathComponent(".venv/bin/" + name)
    try! Data("fixture".utf8).write(to: entry)
    try! fm.removeItem(at: command); try! fm.createSymbolicLink(at: command, withDestinationURL: entry)
    assert(status() == .other)
  }
  try! fm.removeItem(at: command); try! Data("foreign command".utf8).write(to: command)
  assert(status() == .conflict)
  let conflicts = agentConnectionReport(root: root, home: home, environment: [:]).filter { $0.1 == .conflict }
  assert(conflicts.count == 1 && conflicts[0].0 == command)
  assert(String(data: try! Data(contentsOf: command), encoding: .utf8) == "foreign command")
  print("Agent connection: 13 isolated checks PASS; canonical directory URLs and repeated profiles included")
 }
}
