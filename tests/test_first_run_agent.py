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
