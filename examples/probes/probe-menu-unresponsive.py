"""Owned fixture only: suspend its event loop, measure menu read, then resume."""
import hashlib
import json
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tacto.driver import Driver
from tacto.input_audit_process import ProcessInputAudit


def publish(path, value):
    staging=path.with_suffix('.pending')
    staging.write_text(json.dumps(value,ensure_ascii=False))
    staging.replace(path)


def await_file(path, *, child=None, seconds=15):
    deadline=time.monotonic()+seconds
    while not path.exists():
        if child is not None and child.poll() is not None:
            raise RuntimeError('signed client exited before '+path.name)
        if time.monotonic()>=deadline:raise TimeoutError(path.name)
        time.sleep(.02)
    return json.loads(path.read_text())


def signed_client(driver, control):
    session=json.loads((control/'signed-session.json').read_text())
    target=session['target']
    if target!='ai.callabo.tacto.menu-timeout-fixture':raise RuntimeError('wrong owned fixture target')
    before=driver.menu_state(target,['Probe','Responder Command'])
    if before.get('type')!='menu_state':raise RuntimeError('signed initial read failed')
    publish(control/'signed-ready.json',{'before':before})
    await_file(control/'stopped.json')
    started=time.monotonic()
    result=driver.menu_state(target,['Probe','Responder Command'])
    elapsed=time.monotonic()-started
    ping=driver._send({'type':'ping'})
    publish(control/'signed-stalled.json',{'stalled_read':result,'elapsed_seconds':elapsed,
        'ping_after_error':ping})
    await_file(control/'resumed.json')
    after=driver.menu_state(target,['Probe','Responder Command'])
    publish(control/'signed-after.json',{'after':after,'same_driver_instance':True})
    if result.get('error')!='menu_read_failed' or elapsed>=3:
        raise RuntimeError('signed stall classification or latency failed')
    if ping.get('type')!='pong' or after.get('type')!='menu_state':
        raise RuntimeError('same connection recovery failed')


def signed_measure(output, proc, report):
    publish(output/'signed-session.json',{'target':'ai.callabo.tacto.menu-timeout-fixture','pid':proc.pid})
    child=None
    timer=None
    try:
        with (output/'signed-client.log').open('w') as log:
            child=subprocess.Popen([str(ROOT/'launcher/tacto'),'eval','native_coordinate_fixture',
                '--coordinate-control='+str(output),'--menu-unresponsive'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            report.update(await_file(output/'signed-ready.json',child=child))
            report['watchdog_resumed']=False
            def rescue():
                if proc.poll() is None:
                    report['watchdog_resumed']=True
                    proc.send_signal(signal.SIGCONT)
            timer=threading.Timer(8,rescue);timer.daemon=True
            proc.send_signal(signal.SIGSTOP);timer.start()
            try:
                publish(output/'stopped.json',{})
                report.update(await_file(output/'signed-stalled.json',child=child))
            finally:
                timer.cancel()
                if proc.poll() is None:proc.send_signal(signal.SIGCONT)
                publish(output/'resumed.json',{})
            report.update(await_file(output/'signed-after.json',child=child))
            if child.wait(timeout=10)!=0:raise RuntimeError('signed client validation failed')
    finally:
        if timer:timer.cancel()
        if proc.poll() is None:proc.send_signal(signal.SIGCONT)
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()


def main():
    output=Path(sys.argv[1]).resolve()
    output.mkdir(mode=0o700)
    source=(ROOT/'tests/fixtures/NativeMenuFixture.swift').read_bytes()
    report={'completed':False,'source_sha256':hashlib.sha256(source).hexdigest(),
            'scope':'owned signed menu read, same connection recovery' if '--signed' in sys.argv else 'owned direct Python menu read; no signed-helper stall claim'}
    proc=None
    audit=None
    timer=None
    try:
        with tempfile.TemporaryDirectory(prefix='tacto-menu-timeout-') as tmp:
            app=Path(tmp)/'Timeout Fixture.app'
            exe=app/'Contents/MacOS/Fixture'
            exe.parent.mkdir(parents=True)
            target='ai.callabo.tacto.menu-timeout-fixture'
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier':target,'CFBundleExecutable':'Fixture',
                'CFBundlePackageType':'APPL','CFBundleName':'TACTO Timeout Fixture',
                'CFBundleVersion':'1','LSUIElement':True}))
            src=Path(tmp)/'main.swift';src.write_bytes(source)
            subprocess.run(['xcrun','swiftc','-parse-as-library',str(src),'-o',str(exe)],check=True,timeout=30)
            driver=Driver()
            audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
            with audit:
                proc=subprocess.Popen([str(exe),'--timeout','60'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                report['pid']=proc.pid
                deadline=time.monotonic()+10
                while driver._resolve(target,launch=False)!=proc.pid:
                    if proc.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError('owned fixture not registered')
                    time.sleep(.05)
                time.sleep(.3)
                before=driver.menu_state(target,['Probe','Responder Command'])
                report['before']=before
                if before.get('type')!='menu_state':raise RuntimeError('initial read failed')
                if '--signed' in sys.argv:
                    signed_measure(output,proc,report)
                else:
                    report['watchdog_resumed']=False
                    def rescue():
                        if proc.poll() is None:
                            report['watchdog_resumed']=True
                            proc.send_signal(signal.SIGCONT)
                    timer=threading.Timer(8,rescue);timer.daemon=True
                    proc.send_signal(signal.SIGSTOP)
                    timer.start()
                    try:
                        started=time.monotonic()
                        report['stalled_read']=driver.menu_state(target,['Probe','Responder Command'])
                        report['elapsed_seconds']=time.monotonic()-started
                    finally:
                        timer.cancel()
                        if proc.poll() is None:proc.send_signal(signal.SIGCONT)
                    report['after']=driver.menu_state(target,['Probe','Responder Command'])
                if report['stalled_read'].get('error')!='menu_read_failed':raise RuntimeError('wrong stalled classification')
                if report['elapsed_seconds']>=3 or report['watchdog_resumed']:raise RuntimeError('timeout bound failed')
                if report['after'].get('type')!='menu_state':raise RuntimeError('recovery read failed')
                proc.terminate();proc.wait(timeout=5)
            report['audit']=audit.report()
            if report['audit']['status']!='UNCHANGED_SAMPLED':raise RuntimeError('input audit failed')
            report['completed']=True
    except Exception as exc:
        report['error']=repr(exc)
    finally:
        if timer:timer.cancel()
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGCONT);proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        if audit:report['audit']=audit.report()
        report['cleanup']='exact owned child resumed and terminated; no focus restoration'
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report.get(k) for k in ('completed','error','elapsed_seconds','watchdog_resumed')}))
    return 0 if report['completed'] else 1

if __name__=='__main__':raise SystemExit(main())
