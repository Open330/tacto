"""One new owned background TextEdit document; public signed read only."""
from pathlib import Path
import base64
import hashlib
import json
import os
import subprocess
import time


def main(driver, output):
    from nlc.input_audit_process import ProcessInputAudit
    from nlc.native_frontmost import frontmost_pid
    root=Path(__file__).resolve().parents[2]
    target='com.apple.TextEdit'
    sources=['helper/TactoHelper.swift','nlc/helper.py','nlc/trace.py','launcher/tacto.swift',
             'examples/probes/signed-document-rtf.py','nlc/input_audit_process.py','nlc/native_frontmost.py']
    def hashes():return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    report={'passed':False,'scope':'new owned TextEdit RTF; signed range export; no save or edit',
            'source_sha256':hashes(),'owned_window_retained':False}
    audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
    document=output.with_suffix('.owned.rtf').resolve()
    expected='NLC signed RTF witness 한글 😀\n'
    digest=None
    try:
        apps=driver.list_targets().get('apps',[])
        matches=[a for a in apps if a.get('id')==target]
        assert len(matches)==1,'requires one already-running TextEdit process; does not launch or restore the app'
        pid=matches[0]['pid'];report['target_pid']=pid
        current=frontmost_pid()
        assert current is not None and current!=pid,'TextEdit foreground or foreground unavailable; no document opened'
        raw=subprocess.run(['/usr/bin/textutil','-convert','rtf','-stdin','-stdout'],
            input=expected.encode(),capture_output=True,check=True,timeout=10).stdout
        descriptor=os.open(document,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as f:f.write(raw)
        digest=hashlib.sha256(raw).hexdigest()
        report.update(document_path=str(document),source_file_sha256=digest)
        with audit:
            assert frontmost_pid()==current,'foreground changed before owned document open'
            subprocess.run(['/usr/bin/open','-g','-a','/System/Applications/TextEdit.app',str(document)],
                check=True,capture_output=True,timeout=10)
            report['owned_window_retained']=True
            deadline=time.monotonic()+10
            while True:
                text=driver.document_text(target,document_path=str(document),max_utf16_units=1000)
                if text.get('type')=='document_text':break
                assert time.monotonic()<deadline,'owned source document unavailable: '+str(text.get('error'))
                time.sleep(.2)
            assert text['text']==expected and text['document_path']==str(document),text
            token=text['document_token'];report['document_token']=token
            exports=[driver.document_rtf_export(target,token,max_utf16_units=1000,timeout_seconds=3)
                     for _ in range(2)]
            report['exports']=exports
            for result in exports:
                assert result.get('type')=='document_rtf_export',result
                assert result['document_token']==token and result['document_path']==str(document)
                assert result['text_sha256']==hashlib.sha256(expected.encode()).hexdigest()
                assert result['utf16_length']==len(expected.encode('utf-16-le'))//2
                assert all(result[k] is False for k in ('source_format_verified','attachment_completeness_verified','authorizes_save'))
            assert exports[0]['rtf_sha256']==exports[1]['rtf_sha256'],'range exports changed across requests'
            exported=base64.b64decode(exports[0]['rtf_base64'],validate=True)
            report['export_matches_source_bytes']=exported==raw
            output.with_suffix('.range.rtf').write_bytes(exported)
            foreign=driver.document_rtf_export(target,'unknown-owned-token')
            report['unknown_token_refusal']=foreign
            assert foreign.get('effect')=='not_started'
            report['passed']=True
    except Exception as exc:
        report.update(passed=False,error=type(exc).__name__+': '+str(exc))
    finally:
        report['input_audit']=audit.report()
        report['after_source_sha256']=hashes()
        report['source_stable']=report['source_sha256']==report['after_source_sha256']
        report['owned_source_file_unchanged']=digest is not None and document.is_file() and hashlib.sha256(document.read_bytes()).hexdigest()==digest
        if (not report['source_stable'] or not report['owned_source_file_unchanged']
                or report['input_audit'].get('status')!='UNCHANGED_SAMPLED'):report['passed']=False
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report
