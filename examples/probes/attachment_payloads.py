"""Owned AppKit RTFD payload roundtrip, not a GUI save/export proof."""
import hashlib
import json
import os
from pathlib import Path

from examples.probes.signed_popup_flow_replay import reserve_report
from tacto.document_attachments import compare_attachment_payloads,AttachmentEvidenceError


def owned_body():
    from AppKit import NSMutableAttributedString,NSAttributedString,NSTextAttachment
    from Foundation import NSData,NSFileWrapper
    body=NSMutableAttributedString.alloc().initWithString_('Owned attachment evidence: ')
    for name,raw in (('owned-note.txt',b'owned attachment payload'),('owned-bytes.bin',bytes(range(256)))):
        wrapper=NSFileWrapper.alloc().initRegularFileWithContents_(NSData.dataWithBytes_length_(raw,len(raw)))
        wrapper.setPreferredFilename_(name)
        body.appendAttributedString_(NSAttributedString.attributedStringWithAttachment_(
            NSTextAttachment.alloc().initWithFileWrapper_(wrapper)))
    raw=b'owned nested payload'
    leaf=NSFileWrapper.alloc().initRegularFileWithContents_(NSData.dataWithBytes_length_(raw,len(raw)))
    leaf.setPreferredFilename_('inside.txt')
    folder=NSFileWrapper.alloc().initDirectoryWithFileWrappers_({'inside.txt':leaf})
    folder.setPreferredFilename_('owned-folder')
    body.appendAttributedString_(NSAttributedString.attributedStringWithAttachment_(
        NSTextAttachment.alloc().initWithFileWrapper_(folder)))
    return body


def main(output):
    with reserve_report(output) as stream:
        root=Path(__file__).resolve().parents[2]
        names=['tacto/document_attachments.py','examples/probes/attachment_payloads.py','tests/test_document_attachments.py']
        hashes=lambda:{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in names}
        report={'passed':False,'scope':'owned AppKit RTFD serialization payloads, no GUI input',
                'source_before':hashes(),'model_requests':0,'gui_input_requests':0,
                'gui_save_causality_verified':False,'ordinary_app_export_verified':False,
                'comparison_eligible':False,'whole_goal_complete':False}
        try:
            from AppKit import NSAttributedString,NSDocumentTypeDocumentAttribute,NSRTFDTextDocumentType,NSRTFTextDocumentType
            body=owned_body();decoded={}
            for label,kind,suffix in (('rtfd',NSRTFDTextDocumentType,'.flat-rtfd'),('rtf',NSRTFTextDocumentType,'.rtf')):
                data,error=body.dataFromRange_documentAttributes_error_((0,body.length()),{NSDocumentTypeDocumentAttribute:kind},None)
                if data is None or error is not None or data.length()>1024*1024:raise ValueError('bounded_serialization_failed')
                raw=bytes(data);path=Path(output).with_suffix(suffix)
                with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600),'wb') as file:file.write(raw)
                report[label+'_artifact']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
                method=NSAttributedString.alloc().initWithRTFD_documentAttributes_ if label=='rtfd' else NSAttributedString.alloc().initWithRTF_documentAttributes_
                decoded[label]=method(data,None)[0]
                if decoded[label] is None:raise ValueError('serialized_decode_failed')
            report['rtfd_payload_comparison']=compare_attachment_payloads(body,decoded['rtfd'])
            try:
                lost=compare_attachment_payloads(body,decoded['rtf'])
                report['rtf_loss_comparison']=lost
                loss=not lost['captured_attachment_payloads_equal']
            except AttachmentEvidenceError as error:
                report['rtf_loss_comparison']={'status':'unverifiable','reason':str(error),'authorizes_save':False}
                loss=True
            report['rtf_payload_loss_or_unavailability_detected']=loss
            if not report['rtfd_payload_comparison']['captured_attachment_payloads_equal'] or not loss:
                raise ValueError('attachment_evidence_unverified')
            report['passed']=True
        except Exception as error:report.update(passed=False,error_class=type(error).__name__)
        finally:
            report['source_after']=hashes();report['source_stable']=report['source_before']==report['source_after']
            if not report['source_stable']:report['passed']=False
            json.dump(report,stream,indent=2);stream.write('\n')
        return report
