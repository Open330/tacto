import sys
import unittest


@unittest.skipUnless(sys.platform == 'darwin', 'AppKit RTF required')
class RTFSemanticsTests(unittest.TestCase):
    def test_known_rich_source_and_unicode_ranges(self):
        from examples.probes.rtf_semantics import TEXT, rich_source, verify_witness, utf16_span
        result = verify_witness(rich_source())
        self.assertEqual(len(TEXT.encode('utf-16-le')) // 2, result['utf16_length'])
        self.assertEqual((25, 2), utf16_span('한글'))
        self.assertFalse(result['attachments_present'])

    def test_same_text_with_lost_formatting_is_refused(self):
        from examples.probes.rtf_semantics import TEXT, verify_witness
        from AppKit import (NSAttributedString, NSDocumentTypeDocumentAttribute, NSRTFTextDocumentType)
        plain = NSAttributedString.alloc().initWithString_(TEXT)
        data, error = plain.dataFromRange_documentAttributes_error_((0, plain.length()),
            {NSDocumentTypeDocumentAttribute: NSRTFTextDocumentType}, None)
        self.assertIsNone(error)
        with self.assertRaisesRegex(ValueError, 'witness_font_mismatch'):
            verify_witness(bytes(data))

    def test_individual_style_loss_and_color_change_are_refused(self):
        from examples.probes.rtf_semantics import rich_source, verify_witness, utf16_span
        from AppKit import (NSMutableAttributedString, NSFont, NSColor,
            NSFontAttributeName, NSUnderlineStyleAttributeName,
            NSForegroundColorAttributeName, NSDocumentTypeDocumentAttribute,
            NSRTFTextDocumentType)
        from Foundation import NSData
        raw = rich_source()
        for segment, attribute, value, reason in (
                ('signed', NSFontAttributeName, NSFont.fontWithName_size_('Helvetica', 14), 'font'),
                ('RTF', NSUnderlineStyleAttributeName, 0, 'underline'),
                ('한글', NSForegroundColorAttributeName, NSColor.blackColor(), 'color')):
            with self.subTest(segment=segment):
                body, _ = NSMutableAttributedString.alloc().initWithRTF_documentAttributes_(
                    NSData.dataWithBytes_length_(raw, len(raw)), None)
                body.addAttribute_value_range_(attribute, value, utf16_span(segment))
                data, error = body.dataFromRange_documentAttributes_error_((0, body.length()),
                    {NSDocumentTypeDocumentAttribute: NSRTFTextDocumentType}, None)
                self.assertIsNone(error)
                with self.assertRaisesRegex(ValueError, 'witness_' + reason + '_mismatch'):
                    verify_witness(bytes(data))

    def test_whole_range_comparison_detects_unmarked_font_loss(self):
        from examples.probes.rtf_semantics import rich_source,verify_witness,compare_decoded_character_attributes,utf16_span
        from AppKit import (NSMutableAttributedString,NSFont,NSFontAttributeName,
                            NSDocumentTypeDocumentAttribute,NSRTFTextDocumentType)
        from Foundation import NSData
        raw=rich_source();same=compare_decoded_character_attributes(raw,raw)
        self.assertTrue(same['decoded_character_attributes_equal'])
        body,_=NSMutableAttributedString.alloc().initWithRTF_documentAttributes_(
            NSData.dataWithBytes_length_(raw,len(raw)),None)
        span=utf16_span('witness')
        body.addAttribute_value_range_(NSFontAttributeName,NSFont.fontWithName_size_('Helvetica',18),span)
        data,error=body.dataFromRange_documentAttributes_error_((0,body.length()),
            {NSDocumentTypeDocumentAttribute:NSRTFTextDocumentType},None)
        self.assertIsNone(error);mutated=bytes(data)
        verify_witness(mutated)  # The earlier marker oracle misses this loss.
        result=compare_decoded_character_attributes(raw,mutated)
        self.assertTrue(result['decoded_text_equal']);self.assertFalse(result['decoded_character_attributes_equal'])
        self.assertEqual(list(range(span[0],span[0]+span[1])),result['differing_utf16_indices'])
        self.assertEqual({'font':span[1]},result['attribute_difference_counts'])
        self.assertFalse(result['general_source_format_verified']);self.assertFalse(result['authorizes_save'])

    def test_whole_range_comparison_bounds_and_paragraph_change(self):
        from examples.probes.rtf_semantics import rich_source,compare_decoded_character_attributes,verify_witness
        from AppKit import (NSMutableAttributedString,NSMutableParagraphStyle,NSParagraphStyleAttributeName,
                            NSDocumentTypeDocumentAttribute,NSRTFTextDocumentType)
        from Foundation import NSData
        raw=rich_source()
        for left,right,limit in ((raw,raw,True),(raw,raw,1),(None,raw,1000),(b'',raw,1000),(b'not RTF',raw,1000)):
            with self.subTest(limit=limit,left=type(left).__name__),self.assertRaises(ValueError):
                compare_decoded_character_attributes(left,right,max_utf16_units=limit)
        body,_=NSMutableAttributedString.alloc().initWithRTF_documentAttributes_(
            NSData.dataWithBytes_length_(raw,len(raw)),None)
        paragraph=NSMutableParagraphStyle.alloc().init();paragraph.setFirstLineHeadIndent_(12)
        body.addAttribute_value_range_(NSParagraphStyleAttributeName,paragraph,(0,body.length()))
        data,error=body.dataFromRange_documentAttributes_error_((0,body.length()),
            {NSDocumentTypeDocumentAttribute:NSRTFTextDocumentType},None)
        self.assertIsNone(error);mutated=bytes(data);verify_witness(mutated)
        result=compare_decoded_character_attributes(raw,mutated)
        self.assertFalse(result['decoded_character_attributes_equal'])
        self.assertEqual({'paragraph_style':body.length()},result['attribute_difference_counts'])

    def test_owned_font_reader_rejects_changes_and_never_dispatches_input(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from examples.probes.rtf_semantics import read_owned_document_font_signatures
        for mode in ('normal','wrong_pid','changed_snapshot','unreadable','bad_font','reset_failure','text_changed','file_changed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                parent=Path(directory).resolve();parent.chmod(0o700)
                path=parent/'owned.rtf';path.write_bytes(b'owned');path.chmod(0o600)
                driver=Mock();driver.act.side_effect=AssertionError('metadata reader must not dispatch input')
                driver._resolve.return_value=99 if mode=='wrong_pid' else 42
                driver.document_text.side_effect=[{'type':'document_text','text':'A','document_path':str(path),'document_token':'t'},
                    {'type':'document_text','text':'B' if mode=='text_changed' else 'A'}]
                if mode=='file_changed':
                    def document_text(*args,**kwargs):
                        if 'document_token' in kwargs:path.write_bytes(b'changed')
                        return {'type':'document_text','text':'A','document_path':str(path),'document_token':'t'}
                    driver.document_text.side_effect=document_text
                body=object();driver._document_reader._tokens={'t':SimpleNamespace(body=body)}
                values=[]
                for name in ('Helvetica','Arial' if mode=='changed_snapshot' else 'Helvetica'):
                    value=Mock();value.string.return_value='A';value.length.return_value=1
                    value.attributesAtIndex_effectiveRange_.return_value=({'AXFont':{'AXFontName':name,
                        'AXFontSize':True if mode=='bad_font' else 14}},None);values.append(value)
                with patch('tacto.driver.Driver',return_value=driver),patch('ApplicationServices.AXValueCreate',return_value=object()), \
                     patch('ApplicationServices.AXUIElementCopyParameterizedAttributeValue',side_effect=[(-25204,None)] if mode=='unreadable' else [(0,v) for v in values]) as read, \
                     patch('ApplicationServices.AXUIElementSetMessagingTimeout',side_effect=[0,-25204] if mode=='reset_failure' else None,return_value=0) as timeout:
                    if mode=='normal':self.assertEqual([('Helvetica',14.0)],read_owned_document_font_signatures(path,42,'A'))
                    else:
                        with self.assertRaises(ValueError):read_owned_document_font_signatures(path,42,'A')
                    if mode=='wrong_pid':read.assert_not_called()
                    elif mode!='reset_failure':self.assertEqual(0,timeout.call_args.args[1])
                driver.act.assert_not_called()


class BracketedFontEvidenceTests(unittest.TestCase):
    def test_difference_present_before_export_does_not_prove_cause_or_format(self):
        from examples.probes.rtf_semantics import compare_bracketed_font_signatures
        source=[('Helvetica',14.)]*4
        loaded=source.copy();loaded[2]=('AppleSDGothicNeo',14.)
        result=compare_bracketed_font_signatures(source,loaded,loaded,loaded)
        self.assertEqual([2],result['pre_export_source_mismatch_indices'])
        self.assertTrue(result['source_font_difference_observed_before_export'])
        self.assertTrue(result['observed_font_snapshots_equal'])
        self.assertTrue(result['export_matches_both_observed_font_snapshots'])
        for key in ('proves_no_intermediate_change','font_change_origin_verified',
                    'general_source_format_verified','attachment_completeness_verified','authorizes_save'):
            self.assertFalse(result[key])

    def test_change_or_export_mismatch_cannot_be_reported_as_matching_bracket(self):
        from examples.probes.rtf_semantics import compare_bracketed_font_signatures
        source=[('Helvetica',14.)]*2;changed=[('Helvetica',18.)]*2
        for before,exported,after in ((source,changed,changed),(source,changed,source),(source,source,changed)):
            result=compare_bracketed_font_signatures(source,before,exported,after)
            self.assertFalse(result['export_matches_both_observed_font_snapshots'])
        result=compare_bracketed_font_signatures(source,source,source,changed)
        self.assertFalse(result['observed_font_snapshots_equal'])
        self.assertFalse(result['source_font_difference_observed_before_export'])

    def test_invalid_or_unmatched_font_evidence_is_refused(self):
        from examples.probes.rtf_semantics import compare_bracketed_font_signatures
        valid=[('Helvetica',14.)]
        for bad in ([],valid*1001,[('Helvetica',True)],[('Helvetica',float('nan'))],
                    [('Helvetica',float('inf'))],[('Helvetica',0)],[('x'*257,14.)],
                    [['Helvetica',14.]],{'font':'Helvetica'}):
            with self.subTest(bad=type(bad).__name__),self.assertRaises(ValueError):
                compare_bracketed_font_signatures(valid,valid,bad,valid)
        with self.assertRaises(ValueError):compare_bracketed_font_signatures(valid,valid,valid*2,valid)
