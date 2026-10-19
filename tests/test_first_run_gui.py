"""Check first-run controller state in the existing offscreen AppKit fixture."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='darwin', 'AppKit required')
class FirstRunGUIStateTests(unittest.TestCase):
    def test_ready_opening_handoff_and_expiry_control_the_real_button(self):
        source=(ROOT/'setup/main.swift').read_text()
        anchor='        if CommandLine.arguments.contains("--pressed") { connectButton.isHighlighted = true }'
        self.assertEqual(1,source.count(anchor))
        fixture=r'''
        // Test-only fixture injection: render real controls, never launch Terminal.
        detectedAgents = [FirstRunAgent(name:"Codex", executable:URL(fileURLWithPath:"/usr/bin/false"))]
        render()
        assert(startFirstRunButton.isEnabled)
        let pending = directory.appendingPathComponent("owned-launch.command")
        try! Data().write(to:pending)
        firstRunStartedAt=Date(); firstRunLauncher=pending
        render()
        assert(!startFirstRunButton.isEnabled)
        assert(firstRunHint.stringValue.contains("전달하는 중"))
        startFirstRun()
        assert(testTerminalOpenCount == 0, "Opening hold allowed another launch")
        try! FileManager.default.removeItem(at:pending)
        render()
        assert(!startFirstRunButton.isEnabled)
        assert(firstRunHint.stringValue.contains("전달했어요"))
        startFirstRun()
        assert(testTerminalOpenCount == 0, "Handed-off hold allowed another launch")
        contentStack.layoutSubtreeIfNeeded()
        assert(firstRunHint.frame.height + 1 >= firstRunHint.fittingSize.height)
        try! Data().write(to:pending)
        firstRunStartedAt=Date(timeIntervalSinceNow: -firstRunHold-1)
        tickFirstRun()
        assert(startFirstRunButton.isEnabled)
        assert(firstRunStartedAt == nil && firstRunLauncher == nil)
        assert(!FileManager.default.fileExists(atPath:pending.path))
        try! Data("{\"ready\":true,\"opening\":true,\"handoff\":true,\"expiry\":true}\n".utf8)
            .write(to:directory.appendingPathComponent("first-run-state-check.json"))
'''
        with tempfile.TemporaryDirectory(prefix='tacto-first-run-gui-') as temporary:
            folder=Path(temporary)
            # Replace only the external Terminal call in the private test copy.
            # A broken guard fails the counter assertion without starting a CLI.
            external='NSWorkspace.shared.open([command],'
            self.assertEqual(1,source.count(external))
            stub=r'''var testTerminalOpenCount = 0
func testTerminalOpen(_ urls:[URL], withApplicationAt app:URL,
                      configuration:NSWorkspace.OpenConfiguration,
                      completionHandler:(Any?,Error?)->Void) {
    testTerminalOpenCount += 1
    for url in urls { try? FileManager.default.removeItem(at:url) }
    completionHandler(nil,NSError(domain:"test-terminal-disabled",code:1))
}
'''
            instrumented=source.replace(external,'testTerminalOpen([command],')
            instrumented=instrumented.replace('final class Setup:',stub+'final class Setup:')
            (folder/'main.swift').write_text(instrumented.replace(anchor,fixture+'\n'+anchor))
            (folder/'PermissionState.swift').write_bytes((ROOT/'setup/PermissionState.swift').read_bytes())
            binary=folder/'TactoSetup'
            built=subprocess.run(['swiftc','main.swift','PermissionState.swift','-o',str(binary)],cwd=folder,
                                 capture_output=True,text=True,timeout=90)
            self.assertEqual(0,built.returncode,built.stderr)
            output=folder/'snapshot'
            rendered=subprocess.run([str(binary),'--snapshot',str(output),'--standalone','--granted','control,screen',
                                     '--connected','--brand-assets',str(ROOT/'docs/assets')],
                                    capture_output=True,text=True,timeout=45)
            self.assertEqual(0,rendered.returncode,rendered.stderr)
            self.assertEqual({'ready':True,'opening':True,'handoff':True,'expiry':True},
                             json.loads((output/'first-run-state-check.json').read_text()))
            self.assertTrue((output/'setup.png').is_file())
