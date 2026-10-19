"""Owned app changes menu state after the first signed read; never presses it."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import time

TARGET='ai.callabo.nlc.menu-readiness-fixture'


def main(driver,output):
    from tacto.driver import Driver,_attr
    from tacto.helper import InheritedHelperDriver
    from tacto.menu_readiness import wait_menu_enabled
    from tacto.input_audit_process import ProcessInputAudit
    root=Path(__file__).resolve().parents[2]
    paths=list((root/'tacto').glob('*.py'))+list((root/'nlc').glob('*.py'))+list((root/'helper').glob('*.swift'))+[
        Path(__file__).resolve(),root/'examples/eval.py',root/'tests/fixtures/NativeMenuReadinessFixture.swift',
        root/'helper/tacto-helper',root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',root/'launcher/tacto']
    def sources():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'passed':False,'scope':'owned native disabled-to-enabled signed menu reads, no command input','model_calls':0,
            'gui_input_calls':0,'source_before':sources(),'all_dependencies_frozen':False}
    child=None;audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
    try:
        assert isinstance(driver,InheritedHelperDriver)
        local=Driver();assert local._resolve(TARGET,launch=False) is None
        with tempfile.TemporaryDirectory(prefix='nlc-menu-readiness-') as work:
            directory=Path(work);signal=directory/'enable'
            binary=directory/'Fixture.app/Contents/MacOS/Fixture';binary.parent.mkdir(parents=True)
            (binary.parent.parent/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':TARGET,
                'CFBundleExecutable':'Fixture','CFBundleName':'NLC Menu Readiness Fixture','CFBundlePackageType':'APPL','LSUIElement':True}))
            subprocess.run(['xcrun','swiftc','-parse-as-library','-warnings-as-errors',str(root/'tests/fixtures/NativeMenuReadinessFixture.swift'),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            with audit:
                child=subprocess.Popen([str(binary),str(signal)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    until=time.monotonic()+10
                    while local._resolve(TARGET,launch=False)!=child.pid:
                        assert child.poll() is None and time.monotonic()<until
                        time.sleep(.05)
                    def receiving():
                        assert child.poll() is None and local._resolve(TARGET,launch=False)==child.pid
                        result=local.observe(TARGET,'tree_full');assert result.get('type')=='observation'
                        values=[str(_attr(e,'AXValue')) for e in local._latest[TARGET.lower()].elements
                                if _attr(e,'AXIdentifier')=='menu-readiness-state']
                        assert len(values)==1;return values[0]
                    until=time.monotonic()+5
                    while True:
                        try:report['initial']=receiving();break
                        except AssertionError:
                            assert time.monotonic()<until;time.sleep(.05)
                    assert report['initial']=='enabled=false invoked=0'
                    class ReadDriver:
                        calls=0
                        def menu_state(self,target,path):
                            self.calls+=1
                            reply=driver.menu_state(target,path)
                            if self.calls==1:
                                report['first_read_type']=reply.get('type') if reply.get('type') in ('menu_state','error') else 'invalid'
                                report['first_read_enabled']=reply.get('enabled') if type(reply.get('enabled')) is bool else None
                                if reply.get('error') in ('menu_not_found','menu_state_unavailable','target_changed','aborted_by_user'):
                                    report['first_read_error']=reply['error']
                                assert reply.get('type')=='menu_state' and reply.get('enabled') is False
                                # Fixture-owned internal transition after a real
                                # disabled read; not an OS event or app command.
                                with os.fdopen(os.open(signal,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as stream:stream.write('enable')
                            return reply
                        def act(self,*args,**kwargs):raise AssertionError('unexpected input')
                    reader=ReadDriver()
                    result=wait_menu_enabled(reader,TARGET,['Probe','Ready Command'],timeout_seconds=2)
                    report['readiness']=result.to_dict();report['read_count']=reader.calls
                    report['final']=receiving()
                    assert result.state=='ready' and result.enabled_samples[0] is False and result.enabled_samples[-1] is True
                    assert result.read_count==reader.calls and reader.calls>=2
                    assert report['final']=='enabled=true invoked=0'
                    report['passed']=True
                finally:
                    if child is not None:
                        if child.poll() is None:child.terminate()
                        child.wait(timeout=5);report['owned_child_reaped']=True
    except Exception as error:report.update(passed=False,error_class=type(error).__name__)
    finally:
        report['audit']=audit.report();report['source_after']=sources()
        report['source_subset_stable']=report['source_before']==report['source_after']
        report['passed']=report['passed'] and report['source_subset_stable'] and report['audit']['status']=='UNCHANGED_SAMPLED'
        with os.fdopen(fd,'w') as stream:json.dump(report,stream,indent=2)
    return report
