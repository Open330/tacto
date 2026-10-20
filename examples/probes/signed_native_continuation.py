"""One new signed input after reconciliation, verified by an owned AX counter."""
from pathlib import Path
import plistlib
import subprocess
import tempfile
import time

TARGET='ai.callabo.tacto.continuation-fixture'


def run(driver,guard):
    from tacto.driver import Driver,_attr
    from tacto.flow import Selector
    from ApplicationServices import AXUIElementCreateApplication
    local=Driver();process=None
    if local._resolve(TARGET,launch=False) is not None:raise ValueError('continuation fixture already running')
    root=Path(__file__).resolve().parents[2]
    report={'new_input_calls':0,'owned_child_reaped':False}
    with tempfile.TemporaryDirectory(prefix='tacto-native-continuation-') as work:
        binary=Path(work)/'TACTO Continuation.app/Contents/MacOS/Continuation'
        binary.parent.mkdir(parents=True)
        (binary.parent.parent/'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier':TARGET,'CFBundleExecutable':binary.name,
            'CFBundlePackageType':'APPL','CFBundleName':'TACTO Continuation','LSUIElement':True}))
        subprocess.run(['xcrun','swiftc','-parse-as-library',str(root/'tests/fixtures/NativeContinuationFixture.swift'),
            '-o',str(binary)],capture_output=True,check=True,timeout=60)
        try:
            process=subprocess.Popen([str(binary)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while local._resolve(TARGET,launch=False)!=process.pid:
                if process.poll() is not None or time.monotonic()>=deadline:raise ValueError('continuation fixture unavailable')
                time.sleep(.05)
            def receiving():
                if process.poll() is not None or local._resolve(TARGET,launch=False)!=process.pid:
                    raise ValueError('continuation fixture changed')
                pending=[AXUIElementCreateApplication(process.pid)]
                for _ in range(128):
                    if not pending:break
                    node=pending.pop()
                    if _attr(node,'AXIdentifier')=='continuation-state':return _attr(node,'AXValue')
                    pending.extend(_attr(node,'AXChildren') or [])
                raise ValueError('continuation counter unavailable')
            time.sleep(.2);report['before']=receiving()
            if report['before']!='count=0':raise ValueError('continuation initial state invalid')
            obs=driver.observe(TARGET,'tree_full')
            if obs.get('type')!='observation':raise ValueError('continuation observation unavailable')
            index,_=Selector(role='Button',identifier='continue-once').resolve(obs['tree'])
            reply=guard.act(obs['obs_id'],TARGET,{'name':'click','element_index':index},
                expect={'role':'Button','attribute':'Identifier','label':'continue-once','enabled':True},
                risk_class='preapproved',approved=True,intent_text='Continue only the evaluator-owned button task')
            report['new_input_calls']=1;report['dispatch_type']=reply.get('type')
            report['after']=receiving()
            deadline=time.monotonic()+2
            while report['after']=='count=0' and time.monotonic()<deadline:
                time.sleep(.05);report['after']=receiving()
            if reply.get('type')!='acted' or report['after']!='count=1':raise ValueError('continuation input unverified')
        finally:
            if process is not None:
                if process.poll() is None:process.terminate()
                process.wait(timeout=5);report['owned_child_reaped']=True
    return report
