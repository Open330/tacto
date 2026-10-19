"""One new owned background TextEdit document; public signed read only."""
from pathlib import Path
import base64
import hashlib
import json
import os
import subprocess
import stat
import time


def reserve_artifacts(output):
    output=Path(output)
    parent=output.parent
    info=parent.lstat()
    if (not output.is_absolute() or parent.resolve()!=parent or not stat.S_ISDIR(info.st_mode)
            or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700):
        raise ValueError('private_report_directory_required')
    paths={'report':output,'source':output.with_suffix('.owned.rtf'),'range':output.with_suffix('.range.rtf')}
    files={}
    try:
        for key,path in paths.items():
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            files[key]=os.fdopen(fd,'wb')
    except BaseException:
        for key,handle in files.items():
            owned=os.fstat(handle.fileno())
            try:
                named=paths[key].lstat()
                if (named.st_dev,named.st_ino)==(owned.st_dev,owned.st_ino):paths[key].unlink()
            except OSError:pass
            finally:handle.close()
        raise
    return paths,files

def main(driver, output):
    from tacto.input_audit_process import ProcessInputAudit
    from tacto.native_frontmost import frontmost_pid
    root=Path(__file__).resolve().parents[2]
    target='com.apple.TextEdit'
    paths,files=reserve_artifacts(output)
    sources=['helper/TactoHelper.swift','tacto/helper.py','tacto/trace.py','launcher/tacto.swift',
             'examples/probes/signed-document-rtf.py','examples/probes/rtf_semantics.py','tacto/input_audit_process.py','tacto/native_frontmost.py',
             'tacto/driver.py','tacto/native_document.py','tacto/native_document_ax.py','tacto/model_budget.py']
    def hashes():return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    report={'passed':False,'scope':'new owned TextEdit RTF; signed range export; no save or edit',
            'source_sha256':hashes(),'owned_window_retained':False}
    audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
    document=paths['source']
    from examples.probes.rtf_semantics import TEXT, rich_source, verify_witness, compare_decoded_character_attributes, read_owned_document_font_signatures, font_signatures_from_rtf, compare_bracketed_font_signatures
    expected=TEXT
    digest=None
    try:
        apps=driver.list_targets().get('apps',[])
        matches=[a for a in apps if a.get('id')==target]
        assert len(matches)==1,'requires one already-running TextEdit process; does not launch or restore the app'
        pid=matches[0]['pid'];report['target_pid']=pid
        current=frontmost_pid()
        assert current is not None and current!=pid,'TextEdit foreground or foreground unavailable; no document opened'
        raw=rich_source()
        report['source_marker_semantics']=verify_witness(raw)
        files['source'].write(raw);files['source'].flush()
        owned=os.fstat(files['source'].fileno());named=document.lstat()
        assert (named.st_dev,named.st_ino)==(owned.st_dev,owned.st_ino),'owned source identity changed'
        assert stat.S_IMODE(named.st_mode)==0o600 and named.st_nlink==1,'owned source privacy changed'
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
            # Read before requesting any export, not merely after decoding it.
            before_fonts=None
            try:
                before_fonts=read_owned_document_font_signatures(document,pid,expected)
            except Exception as error:
                report['pre_export_AX_font_error_class']=type(error).__name__
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
            report['export_marker_semantics']=verify_witness(exported)
            report['known_marker_semantics_verified']=True
            files['range'].write(exported);files['range'].flush()
            report['decoded_character_attribute_comparison']=compare_decoded_character_attributes(raw,exported)
            report['required_semantic_evidence']='whole decoded character attribute equality'
            try:
                fonts=read_owned_document_font_signatures(document,pid,expected)
                source_fonts=font_signatures_from_rtf(raw);export_fonts=font_signatures_from_rtf(exported)
                assert len(fonts)==len(source_fonts)==len(export_fonts),'font range lengths changed'
                if before_fonts is not None:
                    report['bracketed_AX_font_comparison']=compare_bracketed_font_signatures(
                        source_fonts,before_fonts,export_fonts,fonts)
                report['independent_AX_font_comparison']={
                    'snapshot_stable':True,'utf16_length':len(fonts),
                    'source_mismatch_indices':[i for i,(a,b) in enumerate(zip(fonts,source_fonts)) if a!=b],
                    'export_mismatch_indices':[i for i,(a,b) in enumerate(zip(fonts,export_fonts)) if a!=b],
                    'source_format_verified':False,'font_change_origin_verified':False,'authorizes_save':False}
            except Exception as error:
                report['independent_AX_font_comparison']={'snapshot_stable':False,'error_class':type(error).__name__,
                    'source_format_verified':False,'font_change_origin_verified':False,'authorizes_save':False}
            assert report['decoded_character_attribute_comparison']['decoded_character_attributes_equal'], 'decoded source character attributes changed'
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
        try:
            files['report'].write((json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode())
            files['report'].flush()
        finally:
            for handle in files.values():handle.close()
    return report
