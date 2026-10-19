"""Attachment equality rejects missing/replaced payloads without GUI or input."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tacto.document_attachments import attachment_inventory,compare_attachment_payloads,AttachmentEvidenceError

class Data(bytes):
    def length(self):return len(self)
    def subdataWithRange_(self,span):return Data(self[span[0]:span[0]+span[1]])

class Wrapper:
    def __init__(self,payload=b'owned',*,children=None,name='owned.bin',link=False):
        self.payload=Data(payload);self.children=children;self.name=name;self.link=link
    def isRegularFile(self):return self.children is None and not self.link
    def isDirectory(self):return self.children is not None and not self.link
    def isSymbolicLink(self):return self.link
    def regularFileContents(self):return self.payload
    def fileWrappers(self):return self.children
    def preferredFilename(self):return self.name

class Body:
    def __init__(self,wrapper=None,text='A\ufffc',index=1):
        self.wrapper=wrapper or Wrapper();self.text=text;self.index=index
    def attributedSubstringFromRange_(self,span):
        if span!=(0,self.length()):raise ValueError("invalid captured range")
        return self
    def string(self):return self.text
    def length(self):return len(self.text.encode('utf-16-le'))//2
    def attributesAtIndex_effectiveRange_(self,index,range):
        return ({'attachment':SimpleNamespace(fileWrapper=lambda:self.wrapper)} if index==self.index else {},None)

class AttachmentTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(sys.modules,{'AppKit':SimpleNamespace(NSAttachmentAttributeName='attachment')});p.start();self.addCleanup(p.stop)

    def test_payload_position_name_and_directory_structure_all_count(self):
        source=Body(Wrapper(children={'nested':Wrapper(children={'file':Wrapper()})}))
        same=compare_attachment_payloads(source,source)
        self.assertTrue(same['captured_attachment_payloads_equal'])
        self.assertEqual(3,same['source']['wrapper_entries'])
        self.assertEqual(5,same['source']['payload_bytes'])
        regular=Body(Wrapper())
        for changed in (Body(Wrapper(payload=b'changed')),Body(Wrapper(name='other.bin')),
                        Body(text='\ufffcA',index=0)):
            self.assertFalse(compare_attachment_payloads(regular,changed)['captured_attachment_payloads_equal'])
        changed=Body(Wrapper(children={'file':Wrapper()}))
        self.assertFalse(compare_attachment_payloads(source,changed)['captured_attachment_payloads_equal'])
        for key in ('general_source_format_verified','attachment_completeness_verified','authorizes_save','producer_attribution_verified'):
            self.assertFalse(same[key])
        self.assertNotIn('owned.bin',str(same));self.assertNotIn('owned',str(same))

    def test_repeated_attachment_occurrences_each_consume_resource_budget(self):
        class Repeated(Body):
            def attributesAtIndex_effectiveRange_(self,index,range):
                return ({'attachment':SimpleNamespace(fileWrapper=lambda:self.wrapper)} if index in (1,2) else {},None)
        body=Repeated(text='A\ufffc\ufffc')
        inventory=attachment_inventory(body)
        self.assertEqual([1,2],[row['utf16_index'] for row in inventory['attachment_occurrences']])
        self.assertEqual(10,inventory['payload_bytes']);self.assertEqual(2,inventory['wrapper_entries'])
        with self.assertRaises(AttachmentEvidenceError):attachment_inventory(body,max_total_bytes=5)
        with self.assertRaises(AttachmentEvidenceError):attachment_inventory(body,max_entries=1)

    def test_missing_attachment_or_unmatched_character_is_unknown(self):
        for body in (Body(text='AB'),Body(text='A\ufffc\ufffc'),Body(Wrapper(link=True)),Body(Wrapper(name='../outside'))):
            with self.assertRaises(AttachmentEvidenceError):attachment_inventory(body)

    def test_bounds_refuse_before_payload_acceptance(self):
        for bounds in ({'max_utf16_units':True},{'max_entries':True},{'max_total_bytes':True},
                       {'timeout_seconds':True},{'timeout_seconds':float('nan')},
                       {'max_utf16_units':1},{'max_total_bytes':4}):
            with self.assertRaises(AttachmentEvidenceError):attachment_inventory(Body(),**bounds)
        wide=Body(Wrapper(children={str(i):Wrapper() for i in range(3)}))
        with self.assertRaises(AttachmentEvidenceError):attachment_inventory(wide,max_entries=3)
        cycle=Wrapper(children={});cycle.children['loop']=cycle
        with self.assertRaises(AttachmentEvidenceError):attachment_inventory(Body(cycle))

    def test_growth_after_length_check_uses_bounded_copy_and_refuses(self):
        class OversizedName(str):
            def encode(self,*args,**kwargs):raise AssertionError('unbounded name encoding')
        with self.assertRaisesRegex(AttachmentEvidenceError,'name_unavailable'):
            attachment_inventory(Body(Wrapper(name=OversizedName('x'*100000))))
        class Growing(Data):
            sizes=0
            def length(self):
                self.sizes+=1
                return 5 if self.sizes==1 else len(self)
            def __bytes__(self):raise AssertionError('unbounded materialization')
            def subdataWithRange_(self,span):
                self.requested=span
                return Data(b'owned')
        payload=Growing(b'x'*1000);wrapper=Wrapper();wrapper.payload=payload
        with self.assertRaisesRegex(AttachmentEvidenceError,'payload_changed'):
            attachment_inventory(Body(wrapper),max_total_bytes=5)
        self.assertEqual((0,5),payload.requested)
        class TextGrowth(Body):
            def attributedSubstringFromRange_(self,span):
                self.text='x'*1000
                return Body(text='A\ufffc')
            def string(self):raise AssertionError('unbounded text materialization')
        with self.assertRaisesRegex(AttachmentEvidenceError,'range_mismatch'):
            attachment_inventory(TextGrowth(),max_utf16_units=2)

    def test_final_hash_deadline_never_returns_late_success(self):
        import hashlib
        now=[0.];original=hashlib.sha256
        def late_hash(raw):
            if raw==b'A\xef\xbf\xbc':now[0]=4
            return original(raw)
        with patch('tacto.document_attachments.time.monotonic',side_effect=lambda:now[0]),patch('tacto.document_attachments.hashlib.sha256',side_effect=late_hash):
            with self.assertRaisesRegex(AttachmentEvidenceError,'deadline'):attachment_inventory(Body())

    def test_changed_second_payload_or_late_result_never_returns_inventory(self):
        class Changing(Body):
            reads=0
            def attributesAtIndex_effectiveRange_(self,index,range):
                if index==1:
                    self.reads+=1
                    if self.reads==2:self.wrapper.payload=Data(b'changed')
                return super().attributesAtIndex_effectiveRange_(index,range)
        with self.assertRaisesRegex(AttachmentEvidenceError,'snapshot_changed'):attachment_inventory(Changing())
        with patch('tacto.document_attachments.time.monotonic',side_effect=[0,0,4]):
            with self.assertRaisesRegex(AttachmentEvidenceError,'deadline'):attachment_inventory(Body())

@unittest.skipUnless(sys.platform=='darwin','AppKit RTFD serialization required')
class ActualRTFDAttachmentTests(unittest.TestCase):
    def test_real_filewrapper_rtfd_roundtrip_and_payload_loss(self):
        from AppKit import (NSMutableAttributedString,NSAttributedString,NSTextAttachment,
            NSDocumentTypeDocumentAttribute,NSRTFDTextDocumentType,NSRTFTextDocumentType)
        from Foundation import NSData,NSFileWrapper
        from examples.probes.attachment_payloads import owned_body
        body=owned_body()
        before=attachment_inventory(body)
        self.assertEqual(3,len(before['attachment_occurrences']))
        self.assertEqual(4,before['wrapper_entries'])
        self.assertEqual(len(b'owned attachment payload')+256+len(b'owned nested payload'),before['payload_bytes'])
        data,error=body.dataFromRange_documentAttributes_error_((0,body.length()),
            {NSDocumentTypeDocumentAttribute:NSRTFDTextDocumentType},None)
        self.assertIsNone(error)
        decoded,_=NSAttributedString.alloc().initWithRTFD_documentAttributes_(data,None)
        self.assertIsNotNone(decoded)
        same=compare_attachment_payloads(body,decoded)
        self.assertTrue(same['captured_attachment_payloads_equal'],same)
        rtf,error=body.dataFromRange_documentAttributes_error_((0,body.length()),
            {NSDocumentTypeDocumentAttribute:NSRTFTextDocumentType},None)
        self.assertIsNone(error)
        plain,_=NSAttributedString.alloc().initWithRTF_documentAttributes_(rtf,None)
        # RTF cannot supply these original arbitrary file payloads. A missing
        # wrapper is unknown; a readable changed inventory is explicitly unequal.
        try:lost=compare_attachment_payloads(body,plain)
        except AttachmentEvidenceError:pass
        else:self.assertFalse(lost['captured_attachment_payloads_equal'])

class AttachmentProbeBoundaryTests(unittest.TestCase):
    def test_existing_report_refuses_before_framework_serialization(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from examples.probes.attachment_payloads import main
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder).resolve()/'report.json';output.write_text('KEEP')
            with patch('examples.probes.attachment_payloads.owned_body') as body:
                with self.assertRaises(FileExistsError):main(output)
                body.assert_not_called()
            self.assertEqual('KEEP',output.read_text())
