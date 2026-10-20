"""Read real JSONL fixtures and exercise native controls, never user windows."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from tacto.preferences import load_preferences
from tacto.access_log import AccessLog

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='darwin','AppKit required')
class ActivityCenterTests(unittest.TestCase):
    def test_native_reader_filters_settings_and_private_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder); product=folder/'Tacto'; logs=product/'Logs'; logs.mkdir(parents=True)
            def write(index, command, end=None, target='com.apple.TextEdit', partial=False):
                rows=[dict(schema=1,event='command_start',command=command,at='2026-10-20T00:00:00.000000+00:00',elapsed_ms=0),
                      dict(schema=1,event='access_start',operation='observe',target=target,elapsed_ms=10)]
                if end is not None:
                    rows += [dict(schema=1,event='access_end',operation='observe',target=target,type='observation',elapsed_ms=20),
                             dict(schema=1,event='command_end',exit_code=end,elapsed_ms=30)]
                text=''.join(json.dumps(r)+'\n' for r in rows)+('{"partial":' if partial else '')
                (logs/('tacto-20261020T000000000000Z-'+str(index)*32+'.jsonl')).write_text(text)
            write(1,'first-run',0);write(2,'run',1);write(3,'observe',partial=True)
            sentinel=logs/'notes.txt';sentinel.write_text('KEEP')
            (logs/('tacto-20261020T000000000000Z-'+'4'*32+'.jsonl')).symlink_to(sentinel)
            source=(ROOT/'setup/ActivityCenter.swift').read_text().replace('private ', '')
            (folder/'ActivityCenter.swift').write_text(source)
            (folder/'main.swift').write_text(r'''
import AppKit
let app=NSApplication.shared; app.setActivationPolicy(.prohibited)
let root=URL(fileURLWithPath:CommandLine.arguments[1])
let store=ActivityStore(root:root)
let records=try! store.records()
assert(records.count==3)
assert(records.filter{$0.failed}.count==1)
assert(records.filter{!$0.complete}.count==1)
assert(records.allSatisfy{$0.steps.count==1})
let partial=records.first{!$0.complete}!
assert(!partial.damaged && !partial.steps[0].ended)
let boolEnd=Data("{\"schema\":1,\"event\":\"command_start\",\"command\":\"observe\"}\n{\"schema\":1,\"event\":\"command_end\",\"exit_code\":false}\n".utf8)
assert(!store.parse(boolEnd,url:root,modified:Date()).complete)
let future=Data("{\"schema\":2,\"event\":\"command_end\",\"exit_code\":0}\n".utf8)
assert(store.parse(future,url:root,modified:Date()).damaged)
assert(TactoPreferences(["retention_days":7.0]).retentionDays==30)
let center=ActivityCenter(store:store,refreshAutomatically:false)
try! center.snapshot(to:root.appendingPathComponent("snapshot"))
center.filter.selectedSegment=1;center.applyFilter();assert(center.filtered.count==1)
center.search.stringValue="no-match";center.applyFilter();assert(center.filtered.isEmpty)
center.search.stringValue="";center.filter.selectedSegment=2;center.applyFilter();assert(center.filtered.count==1)
center.navigate(center.settingsButton)
center.logToggle.performClick(nil)
center.presentToggle.performClick(nil)
center.retention.selectItem(at:0);center.saveSettings()
let settings=try! store.preferences()
assert(!settings.loggingEnabled && !settings.presentFirstResult && settings.retentionDays==7)
let permissions=try! FileManager.default.attributesOfItem(atPath:store.preferencesURL.path)
assert((permissions[.posixPermissions] as! NSNumber).intValue==0o600)
try! store.deleteRecords()
assert(try! store.records().isEmpty)
assert(FileManager.default.fileExists(atPath:store.logs.appendingPathComponent("notes.txt").path))
assert(FileManager.default.fileExists(atPath:store.logs.appendingPathComponent("tacto-20261020T000000000000Z-44444444444444444444444444444444.jsonl").path))
print("activity-and-settings-pass")
''')
            binary=folder/'check'
            built=subprocess.run(['swiftc','ActivityCenter.swift','main.swift','-o',str(binary)],cwd=folder,capture_output=True,text=True,timeout=90)
            self.assertEqual(0,built.returncode,built.stderr)
            result=subprocess.run([str(binary),str(product)],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertIn('activity-and-settings-pass',result.stdout)
            prefs=load_preferences(product)
            self.assertEqual((False,False,7),(prefs['logging_enabled'],prefs['present_first_result'],prefs['retention_days']))
            log=AccessLog('observe',root=logs);log.finish(0);log.close()
            self.assertIsNone(log.path)
            self.assertTrue((product/'snapshot/app.png').is_file())
