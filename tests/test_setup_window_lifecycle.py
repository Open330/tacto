"""Exercise setup resize, guide dismissal and focus using real offscreen AppKit."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'AppKit required')
class SetupWindowLifecycleTests(unittest.TestCase):
    def test_refresh_reopen_and_close_preserve_user_window_and_keyboard_focus(self):
        source = (ROOT/'setup/main.swift').read_text()
        anchor = '        if CommandLine.arguments.contains("--pressed") { connectButton.isHighlighted = true }'
        self.assertEqual(1, source.count(anchor))
        fixture = r'''
        let original = window.frame
        let savedHelper = helper
        helper = directory.appendingPathComponent("not-installed/Tacto Helper.app")
        // Readiness, transient errors, long text and repeated checks must not
        // resize/reposition a window the person already chose.
        for index in 0..<12 {
            known = index % 3 != 0
            state.accessibility = index % 2 == 0
            state.screen = index % 4 == 0
            checkInFlight = index % 2 == 1
            notice = index % 3 == 0 ? String(repeating: "상태 확인을 다시 시도해 주세요. ", count: 8) : nil
            permissionDetails = index % 2 == 0
            render()
            assert(window.frame == original, "Permission refresh moved/resized the user's window")
        }
        // Basic app control is usable before screen capture is granted, but
        // the permission section must stay expanded and cannot claim completion.
        notice = nil; permissionDetails = false; watch = nil; checkInFlight = false
        for control in [false, true] {
            for screen in [false, true] {
                known = true; state.accessibility = control; state.screen = screen
                render()
                let complete = control && screen
                assert(rows.allSatisfy { $0.isHidden == complete })
                assert(permissionDetailsButton.isHidden == !complete)
                assert(permissionTitle.stringValue.contains("준비 완료") == complete)
                assert((badge.stringValue == "권한 준비 완료") == complete)
                if control && !screen {
                    assert(permissionTitle.stringValue.contains("1/2"))
                    assert(badge.stringValue.contains("화면 캡처 미설정"))
                    assert(startFirstRunButton.isEnabled == (connection == .current && !detectedAgents.isEmpty))
                }
                known = false; render()
                assert(rows.allSatisfy { !$0.isHidden })
                assert(!permissionTitle.stringValue.contains("준비 완료"))
            }
        }
        // The actual panel must be unable to steal keyboard focus from an
        // authentication window, including when the drag icon is clicked.
        assert(!panel.canBecomeKey && !panel.canBecomeMain)
        assert(panel.becomesKeyOnlyIfNeeded)
        let password = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 120, height: 24))
        window.contentView!.addSubview(password)
        assert(window.makeFirstResponder(password))
        assert(window.firstResponder != nil)
        panel.makeKey()
        assert(!panel.isKeyWindow)
        assert(window.firstResponder === password.currentEditor() || window.firstResponder === password)
        password.removeFromSuperview()
        // Timers are real, but fire only inside this offscreen fixture. A
        // close notification must cancel them before any next placement tick.
        func armGuide() {
            permissionGuideID = UUID()
            watch = PermissionWatch(permission: .control, started: Date())
            watchTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { _ in }
            placeTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { _ in }
        }
        armGuide()
        let oldWatch = watchTimer!, oldPlace = placeTimer!
        panel.performClose(nil)
        assert(permissionGuideID == nil && watch == nil)
        assert(watchTimer == nil && placeTimer == nil)
        assert(!oldWatch.isValid && !oldPlace.isValid)
        // An already queued tick and many explicit reopens cannot resurrect
        // a dismissed guide. Replace only external window activation below.
        testSettingsFrontmost = true
        let beforeDismissedOrders = testPanelOrders
        for _ in 0..<20 { placeAccessory(); reopen() }
        assert(testPanelOrders == beforeDismissedOrders)
        assert(permissionGuideID == nil && watch == nil && !panel.isVisible)
        assert(window.frame == original)
        // Authentication/another app in front pauses an active guide without
        // cancelling the person's explicit permission-setup intent.
        armGuide()
        testSettingsFrontmost = false
        let readsBeforeAuthentication = testSettingsReads, ordersBeforeAuthentication = testPanelOrders
        placeAccessory()
        assert(testSettingsReads == readsBeforeAuthentication && testPanelOrders == ordersBeforeAuthentication)
        assert(permissionGuideID != nil && !panel.isVisible)
        testSettingsFrontmost = true
        placeAccessory()
        assert(testSettingsReads == readsBeforeAuthentication + 1 && testPanelOrders == ordersBeforeAuthentication + 1)
        window.performClose(nil)
        assert(permissionGuideID == nil && watchTimer == nil && placeTimer == nil)
        assert(!applicationShouldTerminateAfterLastWindowClosed(NSApp))
        // Two successful guides can overlap their delayed close callbacks.
        // The first callback must not dismiss the newer completion banner.
        armGuide(); endWatch(granted: true)
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.8))
        armGuide(); endWatch(granted: true)
        let newerGuide = permissionGuideID
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.8))
        assert(newerGuide != nil && permissionGuideID == newerGuide)
        assert(placeTimer?.isValid == true)
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.8))
        assert(permissionGuideID == nil && placeTimer == nil)
        helper = savedHelper
        known = true; state.accessibility = true; state.screen = true
        notice = nil; checkInFlight = false; permissionDetails = false
        render()
        // Existing menu follows all current runs and clears crashes/timeouts.
        runtimeMenuTitle = NSMenuItem(title:"",action:nil,keyEquivalent:"")
        runtimeStop = NSMenuItem(title:"중단",action:nil,keyEquivalent:"")
        let runID = UUID().uuidString
        let run = TactoRuntimeStatus.Run(["schema":1,"uid":getuid(),"pid":getpid(),
            "id":runID,"target":"Fixture","active":true,"stopped":false])!
        runtimeRuns[runID] = (run,Date()); updateRuntimeMenu()
        assert(runtimeMenuTitle!.title.contains("Fixture") && runtimeStop!.isEnabled)
        let stoppedRun = TactoRuntimeStatus.Run(["schema":1,"uid":getuid(),"pid":getpid(),
            "id":runID,"target":"Fixture","active":true,"stopped":true])!
        runtimeRuns[runID] = (stoppedRun,Date()); updateRuntimeMenu()
        assert(runtimeMenuTitle!.title.contains("중단됨") && !runtimeStop!.isEnabled)
        runtimeRuns[runID] = (run,Date(timeIntervalSinceNow:-5)); updateRuntimeMenu()
        assert(runtimeRuns.isEmpty && !runtimeStop!.isEnabled)
        assert(runtimeMenuTitle!.title == "진행 중인 작업 없음")
        try! Data("{\"stable_refresh\":true,\"dismissed_stays_closed\":true,\"reopen_count\":20,\"non_key_panel\":true,\"authentication_priority\":true}\n".utf8)
            .write(to: directory.appendingPathComponent("lifecycle-check.json"))
'''
        # Keep this regression independent of the person's current windows.
        source = source.replace('if connection == .current { workspace().presentCurrent() }', 'if connection == .current { testOpenCount += 1 }')
        source = source.replace('if connection == .current { showAccessLogs() }', 'if connection == .current { testOpenCount += 1 }')
        instrumented = source.replace('        window.makeKeyAndOrderFront(nil)', '        testOpenCount += 1')
        instrumented = instrumented.replace('        NSApp.activate(ignoringOtherApps: true)', '        // Activation disabled in offscreen fixture.')
        instrumented = instrumented.replace('NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.systempreferences"', 'testSettingsFrontmost')
        instrumented = instrumented.replace('guard let settings = settingsWindowFrame() else {', 'guard let settings = testSettingsWindowFrame() else {')
        instrumented = instrumented.replace('if !panel.isVisible { panel.orderFrontRegardless() }', 'if !panel.isVisible { testPanelOrders += 1 }')
        instrumented = instrumented.replace('final class Setup:', 'var testOpenCount = 0\nvar testSettingsFrontmost = false\nvar testSettingsReads = 0\nvar testPanelOrders = 0\nfunc testSettingsWindowFrame() -> CGRect? { testSettingsReads += 1; return CGRect(x: 20, y: 20, width: 700, height: 700) }\nfinal class Setup:')
        instrumented = instrumented.replace(anchor, fixture+'\n'+anchor)
        with tempfile.TemporaryDirectory(prefix='tacto-window-lifecycle-') as folder:
            folder = Path(folder)
            (folder/'main.swift').write_text(instrumented)
            (folder/'PermissionState.swift').write_bytes((ROOT/'setup/PermissionState.swift').read_bytes())
            (folder/'ActivityCenter.swift').write_bytes((ROOT/'setup/ActivityCenter.swift').read_bytes())
            binary = folder/'TactoSetup'
            built = subprocess.run(['swiftc', 'main.swift', 'PermissionState.swift', 'ActivityCenter.swift', str(ROOT/'RuntimeStatus.swift'), '-o', str(binary)],
                                   cwd=folder, capture_output=True, text=True, timeout=90)
            self.assertEqual(0, built.returncode, built.stderr)
            output = folder/'snapshot'
            run = subprocess.run([str(binary), '--snapshot', str(output), '--standalone', '--granted',
                                  'control,screen', '--connected', '--brand-assets', str(ROOT/'docs/assets')],
                                 capture_output=True, text=True, timeout=45)
            self.assertEqual(0, run.returncode, run.stderr)
            self.assertEqual({'stable_refresh': True, 'dismissed_stays_closed': True, 'reopen_count': 20,
                              'non_key_panel': True, 'authentication_priority': True},
                             json.loads((output/'lifecycle-check.json').read_text()))
