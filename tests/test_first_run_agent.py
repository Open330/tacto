"""Exercise native CLI discovery and actual shell argument boundaries."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Swift required')
class FirstRunAgentTests(unittest.TestCase):
    def test_discovery_and_launch_preserve_literal_arguments_without_bypass_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            tool = folder / "agent's tools" / 'codex'
            tool.parent.mkdir()
            output = folder / 'args.json'
            tool.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\n'
                            f'Path({str(output)!r}).write_text(json.dumps(sys.argv[1:]))\n')
            tool.chmod(0o700)
            prompt = "Hello, World! 'quoted' \"double\" $HOME `id` $(touch SHOULD_NOT_EXIST)\nnext line"
            fixture = {'tool': str(tool), 'prompt': prompt, 'folder': str(folder)}
            (folder / 'input.json').write_text(json.dumps(fixture))
            source = folder / 'main.swift'
            source.write_text('''import Foundation
let root = URL(fileURLWithPath: CommandLine.arguments[1])
let input = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("input.json"))) as! [String:String]
let home = root.appendingPathComponent("home")
assert(firstRunAgents(home:home, environment:["PATH":".:relative:"], executable:{ _ in false }).isEmpty)
let one = firstRunAgents(home:home, environment:["PATH":".:relative:/custom tools"], executable:{ $0 == "/custom tools/codex" })
assert(one.count == 1 && one[0].name == "Codex")
let both = firstRunAgents(home:home, environment:["PATH":"/custom:/custom"], executable:{ $0.hasPrefix("/custom/") })
assert(both.map { $0.name } == ["Claude Code", "Codex"])
let fallback = firstRunAgents(home:home, environment:[:], executable:{ $0 == home.appendingPathComponent(".local/bin/claude").path })
assert(fallback.count == 1 && fallback[0].name == "Claude Code")
let real = firstRunAgents(home:home, environment:["PATH":URL(fileURLWithPath:input["tool"]!).deletingLastPathComponent().path])
assert(real.count == 1)
try FileManager.default.createDirectory(at:home.appendingPathComponent(".local/bin/claude"), withIntermediateDirectories:true)
assert(firstRunAgents(home:home, environment:[:]).isEmpty)
let script = firstRunCommand(agent:real[0], prompt:input["prompt"]!, directory:root)
try script.write(to:root.appendingPathComponent("launch.command"), atomically:false, encoding:.utf8)
''')
            binary = folder / 'check'
            subprocess.run(['swiftc', str(ROOT / 'setup/PermissionState.swift'), str(source), '-o', str(binary)],
                           check=True, capture_output=True, timeout=60)
            subprocess.run([str(binary), str(folder)], check=True, capture_output=True, timeout=10)
            script = folder / 'launch.command'
            subprocess.run(['/bin/zsh', str(script)], check=True, capture_output=True, timeout=10)
            self.assertEqual([prompt], json.loads(output.read_text()))
            self.assertFalse((folder / 'SHOULD_NOT_EXIST').exists())
            self.assertFalse(script.exists(), 'one-use launcher should remove itself')

    def test_node_managers_path_and_bounded_start_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            home = folder / 'home'
            # An nvm-installed CLI is a node script; its node sits beside it.
            bin_dir = home / '.nvm/versions/node' / 'v22.13.1' / 'bin'
            older = home / '.nvm/versions/node' / 'v9.11.2' / 'bin'
            for directory in (bin_dir, older):
                directory.mkdir(parents=True)
            output = folder / "node's args.json"
            (bin_dir / 'node').write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n'
                                          f'Path({str(output)!r}).write_text(json.dumps(sys.argv[1:] + [os.environ["PATH"]]))\n')
            (bin_dir / 'codex').write_text('#!/usr/bin/env node\n')
            for tool in (bin_dir / 'node', bin_dir / 'codex'):
                tool.chmod(0o700)
            source = folder / 'main.swift'
            source.write_text('''import Foundation
let root = URL(fileURLWithPath: CommandLine.arguments[1])
let home = root.appendingPathComponent("home")
let bins = nodeManagerBins(home:home, environment:[:], listDirectory:{ path in
    path.hasSuffix(".nvm/versions/node") ? ["v9.11.2", "v22.13.1", "v22.2.0", ".DS_Store"] : [] })
assert(Array(bins.prefix(3)) == [home.path + "/.nvm/versions/node/v22.13.1/bin",
                                 home.path + "/.nvm/versions/node/v22.2.0/bin",
                                 home.path + "/.nvm/versions/node/v9.11.2/bin"], "\\(bins)")
let custom = nodeManagerBins(home:home, environment:["NVM_DIR":"/custom nvm", "FNM_DIR":"relative"],
                             listDirectory:{ $0 == "/custom nvm/versions/node" ? ["v1.0.0"] : [] })
assert(custom == ["/custom nvm/versions/node/v1.0.0/bin"], "\\(custom)")
let fnm = nodeManagerBins(home:home, environment:["FNM_DIR":"/custom fnm"],
                         listDirectory:{ $0 == "/custom fnm/node-versions" ? ["v20.1.0", "v22.0.0"] : [] })
assert(fnm == ["/custom fnm/node-versions/v22.0.0/installation/bin",
               "/custom fnm/node-versions/v20.1.0/installation/bin"])
let found = firstRunAgents(home:home, environment:["PATH":"/usr/bin:/bin"])
assert(found.map { $0.name } == ["Codex"], "\\(found)")
let script = firstRunCommand(agent:found[0], prompt:"Hello 'x' $PATH", directory:root)
try script.write(to:root.appendingPathComponent("launch.command"), atomically:false, encoding:.utf8)
let start = Date(timeIntervalSince1970: 1000)
assert(firstRunStartState(startedAt:nil, launcherExists:true, now:start) == .idle)
assert(firstRunStartState(startedAt:start, launcherExists:true, now:start.addingTimeInterval(1)) == .opening)
assert(firstRunStartState(startedAt:start, launcherExists:false, now:start.addingTimeInterval(1)) == .handedOff)
assert(firstRunStartState(startedAt:start, launcherExists:true, now:start.addingTimeInterval(firstRunHold)) == .idle)
assert(firstRunStartState(startedAt:start, launcherExists:false, now:start.addingTimeInterval(-5)) == .idle)
''')
            binary = folder / 'check'
            subprocess.run(['swiftc', str(ROOT / 'setup/PermissionState.swift'), str(source), '-o', str(binary)],
                           check=True, capture_output=True, timeout=60)
            run = subprocess.run([str(binary), str(folder)], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, run.returncode, run.stderr)
            script = folder / 'launch.command'
            # Terminal's minimal PATH: node is found only through the export.
            subprocess.run(['/bin/zsh', str(script)], check=True, capture_output=True, timeout=10,
                           env={'PATH': '/usr/bin:/bin', 'HOME': str(home)})
            # The agent's bin comes first and the existing PATH is kept after it.
            self.assertEqual([str(bin_dir / 'codex'), "Hello 'x' $PATH", f"{bin_dir}:/usr/bin:/bin"],
                             json.loads(output.read_text()))
            self.assertFalse(script.exists())
            text = (ROOT / 'setup/PermissionState.swift').read_text()
            self.assertNotIn('bash -l', text)
            self.assertNotIn('zsh -l', text)
