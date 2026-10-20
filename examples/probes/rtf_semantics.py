"""Known owned rich-text witness; no GUI input or general format attestation."""
TEXT = 'TACTO signed RTF witness 한글 😀\n'


def utf16_span(segment):
    start = TEXT.index(segment)
    return len(TEXT[:start].encode('utf-16-le')) // 2, len(segment.encode('utf-16-le')) // 2


def rich_source():
    from AppKit import (NSMutableAttributedString, NSFont, NSFontManager,
        NSFontAttributeName, NSBoldFontMask, NSItalicFontMask,
        NSUnderlineStyleAttributeName, NSForegroundColorAttributeName,
        NSColor, NSDocumentTypeDocumentAttribute, NSRTFTextDocumentType)
    body = NSMutableAttributedString.alloc().initWithString_(TEXT)
    font = NSFont.fontWithName_size_('Helvetica', 14)
    if font is None: raise ValueError('witness_font_unavailable')
    body.addAttribute_value_range_(NSFontAttributeName, font, (0, body.length()))
    manager = NSFontManager.sharedFontManager()
    for segment, trait in (('TACTO', NSBoldFontMask), ('signed', NSItalicFontMask)):
        body.addAttribute_value_range_(NSFontAttributeName,
            manager.convertFont_toHaveTrait_(font, trait), utf16_span(segment))
    body.addAttribute_value_range_(NSUnderlineStyleAttributeName, 1, utf16_span('RTF'))
    body.addAttribute_value_range_(NSForegroundColorAttributeName,
        NSColor.colorWithSRGBRed_green_blue_alpha_(.8, .1, .2, 1), utf16_span('한글'))
    data, error = body.dataFromRange_documentAttributes_error_((0, body.length()),
        {NSDocumentTypeDocumentAttribute: NSRTFTextDocumentType}, None)
    if data is None or error is not None: raise ValueError('witness_serialization_failed')
    return bytes(data)


def verify_witness(raw):
    """Validate declared markers independently of RTF serializer byte equality."""
    from AppKit import (NSAttributedString, NSFontAttributeName, NSFontManager,
        NSBoldFontMask, NSItalicFontMask, NSUnderlineStyleAttributeName,
        NSForegroundColorAttributeName, NSColorSpace, NSAttachmentAttributeName)
    from Foundation import NSData
    data = NSData.dataWithBytes_length_(raw, len(raw))
    body, _ = NSAttributedString.alloc().initWithRTF_documentAttributes_(data, None)
    if body is None or str(body.string()) != TEXT:
        raise ValueError('witness_text_mismatch')
    def attribute(name, index):
        return body.attribute_atIndex_effectiveRange_(name, index, None)[0]
    def indices(segment):
        start, count = utf16_span(segment)
        return range(start, start + count)
    manager = NSFontManager.sharedFontManager()
    snapshot = {}
    for segment, trait in (('TACTO', NSBoldFontMask), ('signed', NSItalicFontMask)):
        fonts = [attribute(NSFontAttributeName, i) for i in indices(segment)]
        if any(f is None or not manager.traitsOfFont_(f) & trait
               or abs(f.pointSize() - 14) > .001 or f.familyName() != 'Helvetica' for f in fonts):
            raise ValueError('witness_font_mismatch')
        snapshot[segment] = {'trait_verified': True, 'family': 'Helvetica', 'size': 14}
    if any(attribute(NSUnderlineStyleAttributeName, i) != 1 for i in indices('RTF')):
        raise ValueError('witness_underline_mismatch')
    for i in indices('한글'):
        color = attribute(NSForegroundColorAttributeName, i)
        color = color.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace()) if color is not None else None
        if color is None or any(abs(a-b) > .001 for a,b in zip(
                (color.redComponent(), color.greenComponent(), color.blueComponent(), color.alphaComponent()),
                (.8, .1, .2, 1))):
            raise ValueError('witness_color_mismatch')
    if any(attribute(NSAttachmentAttributeName, i) is not None for i in range(body.length())):
        raise ValueError('witness_unexpected_attachment')
    snapshot.update(underline_verified=True, korean_color_verified=True,
                    utf16_length=body.length(), attachments_present=False)
    return snapshot


def compare_decoded_character_attributes(source, exported, *, max_utf16_units=1000):
    """Compare every decoded character attribute; never attest document format."""
    from AppKit import NSAttributedString, NSAttachmentAttributeName
    from Foundation import NSData
    if type(max_utf16_units) is not int or not 0<max_utf16_units<=100000:
        raise ValueError('invalid_rtf_comparison_bound')
    bodies=[]
    for raw in (source,exported):
        if type(raw) is not bytes or not 0<len(raw)<=1024*1024:
            raise ValueError('invalid_rtf_comparison_input')
        body,_=NSAttributedString.alloc().initWithRTF_documentAttributes_(
            NSData.dataWithBytes_length_(raw,len(raw)),None)
        if body is None:raise ValueError('rtf_comparison_decode_failed')
        if body.length()>max_utf16_units:raise ValueError('rtf_comparison_range_exceeded')
        bodies.append(body)
    before,after=bodies
    text_equal=str(before.string())==str(after.string())
    differences=[];counts={}
    known={'NSFont':'font','NSColor':'foreground_color','NSBackgroundColor':'background_color',
           'NSParagraphStyle':'paragraph_style','NSUnderline':'underline','NSStrikethrough':'strikethrough',
           'NSKern':'kern','NSLigature':'ligature','NSBaselineOffset':'baseline_offset',
           'NSAttachment':'attachment','NSLink':'link'}
    for index in range(min(before.length(),after.length())):
        left=before.attributesAtIndex_effectiveRange_(index,None)[0]
        right=after.attributesAtIndex_effectiveRange_(index,None)[0]
        if left.isEqualToDictionary_(right):continue
        differences.append(index)
        categories=set()
        for key in set(left)|set(right):
            a=left.get(key);b=right.get(key)
            if a is None and b is None:continue
            if a is not None and b is not None and a==b:continue
            categories.add(known.get(str(key),'other'))
        for category in categories:counts[category]=counts.get(category,0)+1
    return {'decoded_text_equal':text_equal,
            'decoded_character_attributes_equal':bool(before.isEqualToAttributedString_(after)),
            'utf16_lengths':[before.length(),after.length()],
            'differing_utf16_indices':differences,'attribute_difference_counts':counts,
            'decoded_attachment_utf16_units':[sum(body.attribute_atIndex_effectiveRange_(
                NSAttachmentAttributeName,index,None)[0] is not None for index in range(body.length())) for body in bodies],
            'general_source_format_verified':False,'attachment_completeness_verified':False,
            'authorizes_save':False}


def font_signatures_from_rtf(raw):
    from AppKit import NSAttributedString,NSFontAttributeName
    from Foundation import NSData
    if type(raw) is not bytes or not 0<len(raw)<=1024*1024:
        raise ValueError('invalid_rtf_font_input')
    body,_=NSAttributedString.alloc().initWithRTF_documentAttributes_(
        NSData.dataWithBytes_length_(raw,len(raw)),None)
    if body is None or body.length()>1000:raise ValueError('rtf_font_range_unavailable')
    result=[]
    for index in range(body.length()):
        font=body.attribute_atIndex_effectiveRange_(NSFontAttributeName,index,None)[0]
        if font is None:raise ValueError('rtf_font_unavailable')
        result.append((str(font.fontName()),float(font.pointSize())))
    return result


def read_owned_document_font_signatures(document_path,expected_pid,expected_text):
    """Independent metadata read of an already owned document; no UI action."""
    import math,os,stat
    from pathlib import Path
    from tacto.driver import Driver
    from tacto.model_budget import model_deadline,remaining_model_seconds
    import time
    from ApplicationServices import (AXValueCreate,kAXValueCFRangeType,
        AXUIElementCopyParameterizedAttributeValue,AXUIElementSetMessagingTimeout)
    from CoreFoundation import CFRangeMake
    if type(expected_pid) is not int or expected_pid<=0 or not isinstance(expected_text,str):
        raise ValueError('owned_font_identity_required')
    path=Path(document_path)
    def identity():
        info=path.lstat();parent=path.parent.lstat()
        if (not path.is_absolute() or path.resolve()!=path or not stat.S_ISREG(info.st_mode)
                or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1
                or not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.getuid()
                or stat.S_IMODE(parent.st_mode)!=0o700):
            raise ValueError('private_owned_document_required')
        return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns
    original=identity();target='com.apple.TextEdit'
    with model_deadline(time.monotonic()+3):
        driver=Driver()
        if driver._resolve(target,launch=False)!=expected_pid:raise ValueError('owned_font_pid_mismatch')
        document=driver.document_text(target,document_path=str(path),max_utf16_units=1000)
        if document.get('type')!='document_text' or document.get('text')!=expected_text or document.get('document_path')!=str(path):
            raise ValueError('owned_font_document_mismatch')
        token=document['document_token'];reader=driver._document_reader
        body=reader._tokens[token].body
        count=len(expected_text.encode('utf-16-le'))//2
        if count>1000:raise ValueError('owned_font_range_exceeded')
        parameter=AXValueCreate(kAXValueCFRangeType,CFRangeMake(0,count))
        if parameter is None:raise ValueError('owned_font_range_unavailable')
        snapshots=[]
        for _ in range(2):
            reader.window_for_token(target,token);remaining_model_seconds(3)
            if AXUIElementSetMessagingTimeout(body,min(.5,remaining_model_seconds(3)))!=0:
                raise ValueError('owned_font_timeout_unavailable')
            try:error,value=AXUIElementCopyParameterizedAttributeValue(body,'AXAttributedStringForRange',parameter,None)
            finally:
                if AXUIElementSetMessagingTimeout(body,0)!=0:raise ValueError('owned_font_timeout_reset_failed')
            remaining_model_seconds(3)
            if error!=0 or value is None or str(value.string())!=expected_text or value.length()!=count:
                raise ValueError('owned_font_snapshot_unavailable')
            snapshot=[]
            for index in range(count):
                remaining_model_seconds(3)
                font=value.attributesAtIndex_effectiveRange_(index,None)[0].get('AXFont')
                if font is None:raise ValueError('owned_font_attribute_unavailable')
                name=font.get('AXFontName');size=font.get('AXFontSize')
                if (not isinstance(name,str) or not 0<len(name)<=256 or isinstance(size,bool)
                        or not isinstance(size,(int,float)) or not math.isfinite(size) or not 0<size<=1000):
                    raise ValueError('owned_font_attribute_malformed')
                snapshot.append((name,float(size)))
            reader.window_for_token(target,token);snapshots.append(snapshot)
        if snapshots[0]!=snapshots[1] or identity()!=original:
            raise ValueError('owned_font_snapshot_changed')
        after=driver.document_text(target,document_token=token,max_utf16_units=1000)
        if after.get('type')!='document_text' or after.get('text')!=expected_text or identity()!=original:
            raise ValueError('owned_font_text_changed')
        remaining_model_seconds(3)
        return snapshots[0]


def compare_bracketed_font_signatures(source, before_export, exported, after_export):
    """Bounded sampled font evidence, never an export/format causality proof."""
    import math
    snapshots=(source,before_export,exported,after_export)
    for snapshot in snapshots:
        if type(snapshot) is not list or not 0<len(snapshot)<=1000:
            raise ValueError('invalid_bracketed_font_range')
        for value in snapshot:
            if (type(value) is not tuple or len(value)!=2 or not isinstance(value[0],str)
                    or not 0<len(value[0])<=256 or isinstance(value[1],bool)
                    or not isinstance(value[1],(int,float)) or not math.isfinite(value[1])
                    or not 0<value[1]<=1000):
                raise ValueError('invalid_bracketed_font_signature')
    if len({len(s) for s in snapshots})!=1:
        raise ValueError('bracketed_font_lengths_changed')
    def differences(left,right):
        return [i for i,(a,b) in enumerate(zip(left,right)) if a!=b]
    prior=differences(source,before_export)
    later=differences(source,after_export)
    before_difference=differences(before_export,exported)
    after_difference=differences(after_export,exported)
    stable=before_export==after_export
    return {'utf16_length':len(source),'observed_font_snapshots_equal':stable,
            'pre_export_source_mismatch_indices':prior,
            'post_export_source_mismatch_indices':later,
            'export_pre_observation_mismatch_indices':before_difference,
            'export_post_observation_mismatch_indices':after_difference,
            'source_font_difference_observed_before_export':bool(prior),
            'export_matches_both_observed_font_snapshots':not before_difference and not after_difference,
            'proves_no_intermediate_change':False,'font_change_origin_verified':False,
            'general_source_format_verified':False,'attachment_completeness_verified':False,
            'authorizes_save':False}
