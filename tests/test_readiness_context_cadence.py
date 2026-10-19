"""Actual Swift scope: no input can consume uncheckpointed readiness results."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ReadinessContextCadenceTests(unittest.TestCase):
    def test_rpc_identity_abort_deadline_metadata_barriers_and_phase_restore(self):
        source=(ROOT/'helper/WindowInput.swift').read_text()
        scope=source[source.index('final class NativeWindowReadScope {'):]
        scope=scope[:scope.index('\n}\n')+3]
        support=r'''
import Foundation
import CoreFoundation
typealias AXUIElement=Int
func CFEqual(_ left:Int,_ right:Int)->Bool {left==right}
enum AXError:Int32 { case success=0,failure = -25200 }
enum NativeWindowInputError:Error { case refused(String),unavailable(String) }
var failSet=false,failReset=false
func AXUIElementSetMessagingTimeout(_ node:Int,_ seconds:Float)->AXError {
 ((seconds>0 && failSet) || (seconds==0 && failReset)) ? .failure:.success
}
func AXUIElementCopyAttributeValue(_ node:Int,_ name:CFString,_ out:inout CFTypeRef?)->AXError { .success }
'''
        checks=r'''
var rows:[[String:Any]]=[]
for mode in ["normal","strict","identity","abort","deadline","metadata_short","metadata_long","throw_body","set_failure","reset_failure"] {
 var copies=0,full=0,fast=0,emitted=0
 var identity=true,stopped=false,metadata=true,refused=false
 let limit=mode=="metadata_short" ? 5:23
 failSet=mode=="set_failure";failReset=mode=="reset_failure"
 func checkIdentity() throws {
  guard identity && !stopped else { throw NativeWindowInputError.refused("identity or abort") }
 }
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+(mode=="deadline" ? 0.2:30),
   incrementalCheck:mode=="strict" ? nil : { fast+=1;try checkIdentity() },
   check:{ full+=1;try checkIdentity();guard metadata else { throw NativeWindowInputError.refused("metadata") } })
 do {
  let value=try scope.withReadinessContext {
   for _ in 0..<limit {
    _=try scope.withTimeout(1) {
     copies+=1
     if copies==3 && mode=="identity" { identity=false }
     if copies==3 && mode=="abort" { stopped=true }
     if copies==1 && mode.hasPrefix("metadata_") { metadata=false }
     if mode=="deadline" { Thread.sleep(forTimeInterval:0.3) }
     return 42
    }
    if copies==3 && mode=="throw_body" { throw NativeWindowInputError.refused("body") }
   }
   return "readiness_result"
  }
  precondition(value=="readiness_result")
  // The host's output/input boundary happens only after full phase validation.
  emitted+=1
 } catch { refused=true }
 let fullBefore=full,fastBefore=fast
 identity=true;stopped=false;metadata=true;failSet=false;failReset=false
 if mode != "deadline" { try! scope.ready() }
 rows.append(["case":mode,"copies":copies,"full":fullBefore,"fast":fastBefore,
              "refused":refused,"emitted":emitted,"outside_full":full-fullBefore,
              "outside_fast":fast-fastBefore,"full_count":scope.fullContextChecks,
              "fast_count":scope.incrementalContextChecks])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/'main.swift';src.write_text(support+scope+checks)
            run=subprocess.run(['swift',str(src)],capture_output=True,text=True,timeout=60)
        self.assertEqual(run.returncode,0,run.stderr)
        rows={row['case']:row for row in json.loads(run.stdout)}
        self.assertEqual(rows['normal']['copies'],23)
        self.assertEqual(rows['normal']['full'],3)
        self.assertEqual(rows['strict']['full'],46)
        self.assertEqual(rows['strict']['fast'],0)
        for name in ['identity','abort','throw_body']:
            self.assertEqual(rows[name]['copies'],3)
        self.assertEqual(rows['metadata_short']['copies'],5)
        self.assertEqual(rows['metadata_long']['copies'],16)
        self.assertEqual(rows['deadline']['copies'],1)
        self.assertEqual(rows['set_failure']['copies'],0)
        self.assertEqual(rows['reset_failure']['copies'],1)
        for name,row in rows.items():
            self.assertEqual(row['emitted'],int(name in ['normal','strict']))
            self.assertEqual(row['refused'],name not in ['normal','strict'])
            self.assertEqual(row['outside_fast'],0)
            self.assertEqual(row['outside_full'],int(name!='deadline'))
            self.assertEqual(row['full_count'],row['full']+row['outside_full'])
            self.assertEqual(row['fast_count'],row['fast'])

    def test_nested_binding_phases_checkpoint_and_restore_after_failure(self):
        source=(ROOT/'helper/WindowInput.swift').read_text()
        scope=source[source.index('final class NativeWindowReadScope {'):]
        scope=scope[:scope.index('\n}\n')+3]
        support=r'''
import Foundation
import CoreFoundation
typealias AXUIElement=Int
func CFEqual(_ left:Int,_ right:Int)->Bool {left==right}
enum AXError:Int32 {case success=0,failure = -25200}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
func AXUIElementSetMessagingTimeout(_ node:Int,_ seconds:Float)->AXError {.success}
func AXUIElementCopyAttributeValue(_ node:Int,_ name:CFString,_ out:inout CFTypeRef?)->AXError {.success}
'''
        checks=r'''
var rows:[[String:Any]]=[]
for mode in ["normal","nested_throw","metadata_changed","identity_changed"] {
 var full=0,fast=0,emitted=0,caughtInner=false,metadata=true,identity=true
 func checkIdentity() throws {if !identity {throw NativeWindowInputError.refused("identity")}}
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+30,
  incrementalCheck:{fast+=1;try checkIdentity()},
  check:{full+=1;try checkIdentity();if !metadata {throw NativeWindowInputError.refused("metadata")}})
 do {
  _=try scope.withBindingContext {
   do {
    _=try scope.withReadinessContext {
     _=try scope.withTimeout(1) {
      if mode=="metadata_changed" {metadata=false}
      if mode=="identity_changed" {identity=false}
     }
     if mode=="nested_throw" {throw NativeWindowInputError.refused("nested")}
     return true
    }
   } catch {caughtInner=true}
   // A caught nested exception must restore the outer read-only phase.
   try scope.ready()
   return true
  }
  emitted+=1
 } catch {}
 let before=full,fastBefore=fast
 metadata=true;identity=true;try! scope.ready()
 rows.append(["case":mode,"emitted":emitted,"caught":caughtInner,
  "outside_full":full-before,"outside_fast":fast-fastBefore,"fast":fastBefore])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'main.swift';path.write_text(support+scope+checks)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        for row in json.loads(result.stdout):
            with self.subTest(case=row['case']):
                self.assertEqual(int(row['case'] in ('normal','nested_throw')),row['emitted'])
                self.assertEqual(row['case']!='normal',row['caught'])
                self.assertEqual(1,row['outside_full'])
                self.assertEqual(0,row['outside_fast'])
                self.assertGreater(row['fast'],0)

    def test_counter_diagnostics_keep_closed_numeric_contract(self):
        from tacto.preparation_diagnostic import preparation_diagnostic
        good={'phase':'binding','reason':'readiness_deadline','elapsed_ms':5,
              'full_context_checks':3,'incremental_context_checks':42}
        self.assertEqual(preparation_diagnostic(good),good)
        self.assertEqual(preparation_diagnostic({**good,'reason':'readiness_batch_failed','readiness_ax_error':-25204})['readiness_ax_error'],-25204)
        for updates in [{'full_context_checks':True},{'incremental_context_checks':-1},
                        {'full_context_checks':1000001},{'readiness_ax_error':True},{'readiness_ax_error':0},{'PRIVATE_TREE':'PRIVATE_CONTENT'}]:
            self.assertIsNone(preparation_diagnostic({**good,**updates}))

    def test_progress_is_bounded_private_and_survives_marker_extraction(self):
        from tacto.preparation_diagnostic import preparation_diagnostic, diagnostic_lines, MARKER
        progress={'pass':1,'windows':14,'completed':2,'index':2,'target':False,
                  'nodes':1234,'total_nodes':3456,'stage':'tree'}
        value={'phase':'binding','reason':'readiness_deadline','elapsed_ms':4999,
               'scoped_ax_operations':3456,'context_elapsed_ms':188,
               'ax_operation_elapsed_ms':4700,'full_context_checks':377,
               'incremental_context_checks':6677,'readiness_progress':progress}
        self.assertEqual(diagnostic_lines(MARKER+json.dumps(value)),[value])
        parsed=preparation_diagnostic(value);progress['nodes']=1
        self.assertEqual(parsed['readiness_progress']['nodes'],1234)
        for updates in ({'nodes':True},{'index':14},{'index':-2},{'completed':15},
                        {'total_nodes':0},{'target':1},{'pass':0},{'stage':'PRIVATE_TITLE'},
                        {'filename':'PRIVATE_FILENAME'},{'windows':1000001}):
            self.assertIsNone(preparation_diagnostic({**value,'readiness_progress':{**progress,**updates}}))
