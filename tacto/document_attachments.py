"""Bounded captured attributed-string attachment payloads; no save authority.

The trusted host supplies already-decoded attributed strings. No AX, paths,
URLs, GUI input, model calls or filesystem writes occur here. Two matching
inventories are sampled stability, not proof of no intermediate changes.
"""
import hashlib
import math
import time


class AttachmentEvidenceError(ValueError):
    pass


def _integer(value,maximum):
    return type(value) is int and 0<=value<=maximum


def _name(value):
    if (not isinstance(value,str) or len(value)>1024 or not value or value in ('.','..')
            or '/' in value or '\\' in value or any(ord(c)<32 or ord(c)==127 for c in value)):
        raise AttachmentEvidenceError('attachment_name_unavailable')
    try:
        if len(value.encode('utf-8'))>1024:raise ValueError()
    except (UnicodeError,ValueError):raise AttachmentEvidenceError('attachment_name_unavailable') from None
    return value


def attachment_inventory(body,*,max_utf16_units=10000,max_entries=256,max_total_bytes=8*1024*1024,
                         timeout_seconds=3):
    """Inspect every attachment occurrence twice with shared resource ceilings.

    FileWrapper regular files/directories are supported; links, unknown wrapper
    types, absent payloads and replacement characters without attachments fail.
    Names become SHA256 path identifiers. Return only closed metadata, no text
    or payload. Limits apply to each pass and include repeated occurrences.
    Framework calls have a soft deadline; late results cannot be accepted.
    """
    if (type(max_utf16_units) is not int or not 0<max_utf16_units<=100000
            or type(max_entries) is not int or not 0<max_entries<=4096
            or type(max_total_bytes) is not int or not 0<max_total_bytes<=64*1024*1024
            or type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds)
            or not 0<timeout_seconds<=10):raise AttachmentEvidenceError('invalid_attachment_bound')
    from AppKit import NSAttachmentAttributeName
    deadline=time.monotonic()+timeout_seconds
    def check():
        if time.monotonic()>=deadline:raise AttachmentEvidenceError('attachment_deadline')
    def call(method,*args):
        check();value=method(*args);check();return value
    def snapshot():
        count=call(body.length)
        if not _integer(count,max_utf16_units):raise AttachmentEvidenceError('attachment_range_exceeded')
        # Capture only the bounded full range into an immutable native substring.
        # Attachment wrappers may still mutate; the second inventory checks them.
        captured=call(body.attributedSubstringFromRange_,(0,count))
        if call(captured.length)!=count or call(body.length)!=count:
            raise AttachmentEvidenceError('attachment_range_mismatch')
        text=str(call(captured.string))
        # Bound before materializing the UTF16 buffer, including malformed text.
        if len(text)>max_utf16_units:raise AttachmentEvidenceError('attachment_range_exceeded')
        try:encoded=text.encode('utf-16-le')
        except UnicodeError:raise AttachmentEvidenceError('attachment_text_unavailable') from None
        if len(encoded)!=count*2:raise AttachmentEvidenceError('attachment_range_mismatch')
        records=[];total=0;entries=0
        def wrapper_rows(wrapper):
            nonlocal total,entries
            rows=[];stack=[(wrapper,(),0)]
            while stack:
                check();node,path,depth=stack.pop();entries+=1
                if entries>max_entries or depth>16:raise AttachmentEvidenceError('attachment_entries_exceeded')
                flags=[call(node.isRegularFile),call(node.isDirectory),call(node.isSymbolicLink)]
                if any(type(flag) is not bool for flag in flags) or sum(flags)!=1 or flags[2]:
                    raise AttachmentEvidenceError('attachment_wrapper_unsupported')
                path_hash=hashlib.sha256('/'.join(path).encode('utf-8')).hexdigest()
                if flags[0]:
                    data=call(node.regularFileContents)
                    if data is None:raise AttachmentEvidenceError('attachment_payload_unavailable')
                    size=call(data.length)
                    if not _integer(size,max_total_bytes-total):raise AttachmentEvidenceError('attachment_bytes_exceeded')
                    total+=size
                    bounded=call(data.subdataWithRange_,(0,size))
                    if call(bounded.length)!=size or call(data.length)!=size:
                        raise AttachmentEvidenceError('attachment_payload_changed')
                    raw=bytes(bounded);check()
                    if len(raw)!=size:raise AttachmentEvidenceError('attachment_payload_changed')
                    rows.append({'path_sha256':path_hash,'kind':'file','bytes':size,
                                 'sha256':hashlib.sha256(raw).hexdigest()})
                else:
                    children=call(node.fileWrappers)
                    if children is None or len(children)>max_entries-entries-len(stack):
                        raise AttachmentEvidenceError('attachment_entries_exceeded')
                    names=sorted(_name(name) for name in children)
                    rows.append({'path_sha256':path_hash,'kind':'directory'})
                    stack.extend((children[name],path+(name,),depth+1) for name in reversed(names))
            return sorted(rows,key=lambda row:row['path_sha256'])
        for index in range(count):
            attributes=call(captured.attributesAtIndex_effectiveRange_,index,None)[0]
            attachment=attributes.get(NSAttachmentAttributeName)
            replacement=encoded[index*2:index*2+2]==b'\xfc\xff'
            if (attachment is None)!= (not replacement):
                raise AttachmentEvidenceError('attachment_character_mismatch')
            if attachment is None:continue
            wrapper=call(attachment.fileWrapper)
            if wrapper is None:raise AttachmentEvidenceError('attachment_wrapper_unavailable')
            filename=_name(call(wrapper.preferredFilename))
            records.append({'utf16_index':index,'filename_sha256':hashlib.sha256(filename.encode('utf-8')).hexdigest(),
                            'entries':wrapper_rows(wrapper)})
        check()
        if call(body.length)!=count:raise AttachmentEvidenceError('attachment_range_mismatch')
        result={'utf16_length':count,'text_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'attachment_occurrences':records,'payload_bytes':total,'wrapper_entries':entries}
        check();return result
    try:
        first=snapshot();second=snapshot()
        if first!=second:raise AttachmentEvidenceError('attachment_snapshot_changed')
        result={**first,'sampled_stable':True,'proves_no_intermediate_change':False,
                'general_source_format_verified':False,'authorizes_save':False}
        check();return result
    except AttachmentEvidenceError:raise
    except Exception as error:raise AttachmentEvidenceError('attachment_snapshot_unavailable') from error


def compare_attachment_payloads(source,exported,**bounds):
    """Compare captured payloads/positions/names; not source-format or causality."""
    limit=bounds.get('timeout_seconds',3)
    if type(limit) not in (int,float) or not math.isfinite(limit) or not 0<limit<=10:
        raise AttachmentEvidenceError('invalid_attachment_bound')
    deadline=time.monotonic()+limit
    def inventory(body):
        left=deadline-time.monotonic()
        if left<=0:raise AttachmentEvidenceError('attachment_deadline')
        return attachment_inventory(body,**{**bounds,'timeout_seconds':left})
    before=inventory(source);after=inventory(exported)
    fields=('utf16_length','text_sha256','attachment_occurrences','payload_bytes','wrapper_entries')
    same=all(before[name]==after[name] for name in fields)
    result={'captured_attachment_payloads_equal':same,'source':before,'exported':after,
            'general_source_format_verified':False,'attachment_completeness_verified':False,
            'authorizes_save':False,'producer_attribution_verified':False}
    if time.monotonic()>=deadline:raise AttachmentEvidenceError('attachment_deadline')
    return result
