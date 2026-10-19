"""One owned TextEdit RTFD source and signed RTF export; no save/edit/send."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from examples.probes.attachment_payloads import owned_body
from examples.probes.signed_popup_flow_replay import ROOT,reserve_report
from tacto.document_attachments import compare_attachment_payloads,AttachmentEvidenceError,_name


def write_owned_package(directory,wrapper):
    """Persist only the newly-created fixed witness; never an existing package."""
    directory.mkdir(mode=0o700)
    def write(node,path,depth):
        if depth>16 or node.isSymbolicLink():raise ValueError('fixture_wrapper_unsupported')
        if node.isDirectory():
            children=node.fileWrappers()
            if len(children)>32:raise ValueError('fixture_wrapper_bound')
            for name,child in children.items():
                child_path=path/_name(name)
                if child.isDirectory():child_path.mkdir(mode=0o700)
                write(child,child_path,depth+1)
        elif node.isRegularFile():
            data=node.regularFileContents()
            if data is None or data.length()>1024*1024:raise ValueError('fixture_payload_bound')
            with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600),'wb') as file:
                file.write(bytes(data))
        else:raise ValueError('fixture_wrapper_unsupported')
    if not wrapper.isDirectory():raise ValueError('fixture_directory_required')
    write(wrapper,directory,0)


def package_manifest(directory):
    def root_identity():
        root=directory.lstat()
        if (not directory.is_absolute() or directory.resolve()!=directory or not stat.S_ISDIR(root.st_mode)
                or root.st_uid!=os.getuid() or stat.S_IMODE(root.st_mode)!=0o700):
            raise ValueError('fixture_package_changed')
        return root.st_dev,root.st_ino,root.st_uid,stat.S_IMODE(root.st_mode)
    original=root_identity();rows={};entries=0
    def identity(info):
        return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_uid,info.st_nlink,info.st_mode)
    for path in directory.rglob('*'):
        entries+=1
        if entries>64 or len(path.relative_to(directory).parts)>16:raise ValueError('fixture_package_bound')
        info=path.lstat()
        if path.is_symlink() or info.st_uid!=os.getuid():raise ValueError('fixture_identity_changed')
        if stat.S_ISDIR(info.st_mode):
            if stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('fixture_privacy_changed')
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>1024*1024:
            raise ValueError('fixture_file_changed')
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as file:
            if identity(os.fstat(file.fileno()))!=identity(info):raise ValueError('fixture_identity_changed')
            raw=file.read(1024*1024+1)
            if identity(os.fstat(file.fileno()))!=identity(info) or identity(path.lstat())!=identity(info):
                raise ValueError('fixture_identity_changed')
        if len(raw)>1024*1024:raise ValueError('fixture_payload_bound')
        rows[str(path.relative_to(directory))]={'sha256':hashlib.sha256(raw).hexdigest(),'identity':identity(info)}
    if root_identity()!=original:raise ValueError('fixture_package_changed')
    return {'root_identity':original,'files':rows}


def main(driver,output):
    with reserve_report(output) as stream:
        names=['examples/probes/signed_document_attachments.py','examples/probes/attachment_payloads.py',
               'tacto/document_attachments.py','tacto/native_document.py','tacto/helper.py','helper/TactoHelper.swift',
               'helper/Tacto Helper.app/Contents/MacOS/TactoHelper','launcher/tacto','examples/eval.py']
        hashes=lambda:{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in names}
        report={'passed':False,'scope':'owned background TextEdit RTFD and signed RTF range export',
                'source_before':hashes(),'signed_input_requests':0,'owned_document_open_requests':0,
                'model_requests':0,'gui_save_causality_verified':False,'comparison_eligible':False,
                'whole_goal_complete':False,'owned_window_retained':False}
        audit=None;source=None;before=None
        try:
            from tacto.helper import InheritedHelperDriver
            if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():raise ValueError('signed_channel_required')
            if 'evidence.document_rtf_export' not in driver.capabilities:raise ValueError('signed_export_required')
            from tacto.native_frontmost import frontmost_pid
            from tacto.input_audit_process import ProcessInputAudit
            from AppKit import NSDocumentTypeDocumentAttribute,NSRTFDTextDocumentType,NSAttributedString
            from Foundation import NSData
            target='com.apple.TextEdit';matches=[row for row in driver.list_targets().get('apps',[]) if row.get('id')==target]
            if len(matches)!=1:raise ValueError('already_running_textedit_required')
            pid=matches[0]['pid'];foreground=frontmost_pid()
            if foreground is None or foreground==pid:raise ValueError('background_textedit_required')
            body=owned_body();expected=str(body.string())
            wrapper,error=body.fileWrapperFromRange_documentAttributes_error_((0,body.length()),{NSDocumentTypeDocumentAttribute:NSRTFDTextDocumentType},None)
            if wrapper is None or error is not None:raise ValueError('fixture_serialization_failed')
            source=Path(output).with_suffix('.owned.rtfd');write_owned_package(source,wrapper)
            before=package_manifest(source)
            report.update(source_package=str(source),source_file_count=len(before['files']),target_pid=pid)
            audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
            with audit:
                if frontmost_pid()!=foreground:raise ValueError('foreground_changed_before_open')
                report['owned_document_open_requests']=1
                subprocess.run(['/usr/bin/open','-g','-a','/System/Applications/TextEdit.app',str(source)],check=True,capture_output=True,timeout=10)
                report['owned_window_retained']=True
                deadline=time.monotonic()+10
                while True:
                    if frontmost_pid()!=foreground:raise ValueError('foreground_changed_after_open')
                    text=driver.document_text(target,document_path=str(source),max_utf16_units=1000)
                    if text.get('type')=='document_text':break
                    if time.monotonic()>=deadline:raise ValueError('owned_document_unavailable')
                    time.sleep(.2)
                if text.get('document_path')!=str(source):raise ValueError('owned_document_path_changed')
                report['observed_text_matches_source']=text.get('text')==expected
                report['observed_utf16_length']=len(text['text'].encode('utf-16-le'))//2
                export=driver.document_rtf_export(target,text['document_token'],max_utf16_units=1000,timeout_seconds=3)
                report['export']=export
                if (export.get('type')!='document_rtf_export' or export.get('document_token')!=text['document_token']
                        or export.get('document_path')!=str(source)
                        or export.get('text_sha256')!=hashlib.sha256(text['text'].encode('utf-8')).hexdigest()
                        or export.get('utf16_length')!=len(text['text'].encode('utf-16-le'))//2
                        or any(export.get(key) is not False for key in ('source_format_verified','attachment_completeness_verified','authorizes_save'))):
                    raise ValueError('signed_export_unavailable')
                after=driver.document_text(target,document_token=text['document_token'],max_utf16_units=1000)
                if after.get('type')!='document_text' or after.get('text')!=text['text'] or after.get('document_path')!=str(source):
                    raise ValueError('owned_document_changed_after_export')
                raw=base64.b64decode(export['rtf_base64'],validate=True)
                with os.fdopen(os.open(Path(output).with_suffix('.exported.rtf'),os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600),'wb') as file:file.write(raw)
                decoded,_=NSAttributedString.alloc().initWithRTF_documentAttributes_(NSData.dataWithBytes_length_(raw,len(raw)),None)
                if decoded is None:raise ValueError('signed_export_decode_failed')
                try:report['attachment_payload_comparison']=compare_attachment_payloads(body,decoded)
                except AttachmentEvidenceError as error:
                    report['attachment_payload_comparison']={'captured_attachment_payloads_equal':False,'status':'unverifiable','reason':str(error),'authorizes_save':False}
                report['probe_completed']=True
                report['passed']=report['observed_text_matches_source'] and report['attachment_payload_comparison']['captured_attachment_payloads_equal']
        except Exception as error:report.update(passed=False,error_class=type(error).__name__,error=str(error))
        finally:
            try:
                report['source_after']=hashes();report['source_stable']=report['source_before']==report['source_after']
            except Exception as error:
                report['source_stable']=False;report['source_hash_error_class']=type(error).__name__
            try:report['owned_source_package_unchanged']=source is not None and before is not None and package_manifest(source)==before
            except Exception as error:
                report['owned_source_package_unchanged']=False
                report['source_check_error_class']=type(error).__name__
            try:report['input_audit']=audit.report() if audit else {'status':'NOT_STARTED'}
            except Exception as error:
                report['input_audit']={'status':'INCONCLUSIVE','error_class':type(error).__name__}
            if not report['source_stable'] or not report['owned_source_package_unchanged'] or report['input_audit']['status']!='UNCHANGED_SAMPLED':report['passed']=False
            json.dump(report,stream,indent=2);stream.write('\n')
        return report
