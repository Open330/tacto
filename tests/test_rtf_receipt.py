"""No UI: synthetic RTF artifacts exercise native host receipt boundaries."""
import hashlib
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

from nlc.artifact_receipt import ArtifactReceiptError
from nlc.commit_evidence import make_event, evidence_error
from nlc.rtf_receipt import FreshRTFReceipt, _ReadbackPending


class RTFReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.directory.chmod(0o755)
        self.name = 'nlc-test-' + uuid.uuid4().hex + '.rtf'
        self.path = self.directory / self.name
        self.event = make_event({'name':'click','element_index':5}, {'write_persistent'}, 'com.apple.TextEdit')
        self.response = {'type':'acted'}

    def receipt(self, **kwargs):
        return FreshRTFReceipt(self.directory, self.name, 'Hello',
                               document_path=kwargs.pop('document_path', lambda: str(self.path)),
                               timeout_seconds=kwargs.pop('timeout_seconds', .08), **kwargs)

    def test_owned_public_read_directory_raw_hash_and_decoded_text(self):
        adapter=self.receipt()
        context=adapter.prepare_commit(self.event)
        raw=b'{\\rtf1\\ansi Hello}'
        self.path.write_bytes(raw)
        with patch.object(adapter,'_decode',return_value='Hello'):
            proof=adapter.after_commit(self.event,self.response,context)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),proof.evidence.sha256)
        self.assertNotEqual(hashlib.sha256(b'Hello').hexdigest(),proof.evidence.sha256)
        self.assertEqual({'저장 위치':str(self.directory),'저장한 이름':self.name},adapter.commit_expected)
        self.assertIsNone(evidence_error({**self.event,'commit_evidence':proof},adapter.commit_expected))

    @unittest.skipUnless(Path('/usr/bin/textutil').is_file(),'macOS textutil decoder')
    def test_actual_decoder_preserves_text_newline_and_empty_document(self):
        for text,raw in [('Hello',b'{\\rtf1\\ansi Hello}'),
                         ('Hello\n',b'{\\rtf1\\ansi Hello\\par}'),
                         ('',b'{\\rtf1\\ansi }'),
                         ('한글',b'{\\rtf1\\ansi\\uc1 \\u54620?\\u44544?}')]:
            with self.subTest(text=text):
                if self.path.exists():self.path.unlink()
                adapter=FreshRTFReceipt(self.directory,self.name,text,document_path=lambda:str(self.path))
                context=adapter.prepare_commit(self.event)
                self.path.write_bytes(raw)
                self.assertIsNotNone(adapter.after_commit(self.event,self.response,context))

    def test_existing_file_rejected_at_construction_and_at_prepare(self):
        self.path.write_bytes(b'original')
        with self.assertRaises(ArtifactReceiptError):self.receipt()
        self.assertEqual(b'original',self.path.read_bytes())
        self.path.unlink()
        adapter=self.receipt()
        self.path.write_bytes(b'new competitor')
        with self.assertRaises(ArtifactReceiptError):adapter.prepare_commit(self.event)
        self.assertEqual(b'new competitor',self.path.read_bytes())

    def test_reuse_and_mismatched_event_cannot_issue_receipt(self):
        adapter=self.receipt()
        context=adapter.prepare_commit(self.event)
        self.path.write_bytes(b'{\\rtf1 Hello}')
        other=make_event({'name':'click','element_index':6},{'write_persistent'},'com.apple.TextEdit')
        with self.assertRaises(ArtifactReceiptError):adapter.after_commit(other,self.response,context)
        with patch.object(adapter,'_decode',return_value='Hello'):
            self.assertIsNotNone(adapter.after_commit(self.event,self.response,context))
        with self.assertRaises(ArtifactReceiptError):adapter.after_commit(self.event,self.response,context)
        with self.assertRaises(ArtifactReceiptError):adapter.prepare_commit(self.event)

    def test_missing_corrupt_wrong_path_and_whitespace_do_not_pass(self):
        adapter=self.receipt()
        self.assertIsNone(adapter.after_commit(self.event,self.response,adapter.prepare_commit(self.event)))
        for decoded,docpath in [('corrupt',str(self.path)),('Hello',str(self.directory/'wrong.rtf')),('Hello\n',str(self.path))]:
            if self.path.exists():self.path.unlink()
            adapter=self.receipt(document_path=lambda:docpath)
            context=adapter.prepare_commit(self.event)
            self.path.write_bytes(b'{\\rtf1 Hello}')
            with patch.object(adapter,'_decode',return_value=decoded):
                self.assertIsNone(adapter.after_commit(self.event,self.response,context))

    def test_zero_budget_and_late_document_callback_never_pass(self):
        adapter=self.receipt(timeout_seconds=0)
        context=adapter.prepare_commit(self.event)
        self.path.write_bytes(b'{\\rtf1 Hello}')
        self.assertIsNone(adapter.after_commit(self.event,self.response,context))
        self.path.unlink()
        now=[0.0]
        def docpath():now[0]=2;return str(self.path)
        adapter=self.receipt(timeout_seconds=1,clock=lambda:now[0],sleep=lambda _:None,document_path=docpath)
        context=adapter.prepare_commit(self.event)
        self.path.write_bytes(b'{\\rtf1 Hello}')
        with patch.object(adapter,'_decode',return_value='Hello'):
            self.assertIsNone(adapter.after_commit(self.event,self.response,context))

    def test_symlink_hardlink_and_group_writable_directory_rejected(self):
        adapter=self.receipt()
        context=adapter.prepare_commit(self.event)
        other=self.directory/'other.rtf';other.write_bytes(b'{\\rtf1 Hello}')
        self.path.symlink_to(other)
        with self.assertRaises(OSError):adapter.after_commit(self.event,self.response,context)
        self.path.unlink()
        adapter=self.receipt();context=adapter.prepare_commit(self.event)
        os.link(other,self.path)
        with self.assertRaises(ArtifactReceiptError):adapter.after_commit(self.event,self.response,context)
        self.path.unlink()
        self.directory.chmod(0o775)
        with self.assertRaises(ArtifactReceiptError):self.receipt()

    def test_directory_permission_drift_and_not_started_are_rejected(self):
        adapter=self.receipt();context=adapter.prepare_commit(self.event)
        self.directory.chmod(0o700)
        with self.assertRaises(ArtifactReceiptError):adapter.after_commit(self.event,self.response,context)
        adapter=self.receipt();context=adapter.prepare_commit(self.event)
        with self.assertRaises(ArtifactReceiptError):
            adapter.after_commit(self.event,{'type':'error','effect':'not_started'},context)

    def test_user_chosen_korean_basename_is_fresh_and_existing_file_is_preserved(self):
        name='보고서.rtf'
        path=self.directory/name
        adapter=FreshRTFReceipt(self.directory,name,'Hello',document_path=lambda:str(path))
        context=adapter.prepare_commit(self.event)
        raw=b'{\\rtf1 Hello}'
        path.write_bytes(raw)
        with patch.object(adapter,'_decode',return_value='Hello'):
            proof=adapter.after_commit(self.event,self.response,context)
        self.assertEqual(str(path),proof.evidence.path)
        self.assertEqual(name,adapter.commit_expected['저장한 이름'])
        with self.assertRaises(ArtifactReceiptError):
            FreshRTFReceipt(self.directory,name,'replacement',document_path=lambda:str(path))
        self.assertEqual(raw,path.read_bytes())

    def test_invalid_filename_and_non_rtf_are_rejected(self):
        for name in ('', '.', '..', '.rtf', '..rtf', '...rtf', '../'+self.name,
                     'folder/'+self.name, 'folder\\'+self.name, 'nul\0.rtf', self.name+'.txt'):
            with self.assertRaises(ValueError):
                FreshRTFReceipt(self.directory,name,'Hello',document_path=lambda:str(self.path))
        adapter=self.receipt();context=adapter.prepare_commit(self.event)
        self.path.write_bytes(b'not RTF')
        self.assertIsNone(adapter.after_commit(self.event,self.response,context))

    def test_decoder_timeout_and_after_decode_mutation_never_produce_proof(self):
        adapter=self.receipt(timeout_seconds=.08)
        context=adapter.prepare_commit(self.event)
        self.path.write_bytes(b'{\\rtf1 Hello}')
        original_popen=subprocess.Popen
        def slow_decoder(argv,**kwargs):
            return original_popen([sys.executable,'-c','import time;time.sleep(20)'],**kwargs)
        started=time.monotonic()
        with patch('nlc.rtf_receipt.subprocess.Popen',side_effect=slow_decoder):
            self.assertIsNone(adapter.after_commit(self.event,self.response,context))
        self.assertLess(time.monotonic()-started,1)
        self.path.unlink()
        def mutate():
            self.path.write_bytes(b'changed after read')
            return str(self.path)
        adapter=self.receipt(document_path=mutate)
        context=adapter.prepare_commit(self.event)
        self.path.write_bytes(b'{\\rtf1 Hello}')
        with patch.object(adapter,'_decode',return_value='Hello'), self.assertRaises(ArtifactReceiptError):
            adapter.after_commit(self.event,self.response,context)

    def fake_decoder(self, source):
        original = subprocess.Popen
        children = []
        def start(argv, **kwargs):
            child = original([sys.executable, '-c', source], **kwargs)
            children.append(child)
            return child
        return start, children

    def test_decoder_flood_stops_at_byte_limit_and_reaps_child(self):
        adapter = self.receipt(timeout_seconds=2)
        adapter._MAX_TEXT_BYTES = 65536
        adapter._decode_deadline = adapter._clock() + 2
        start, children = self.fake_decoder(
            "import os\nwhile True: os.write(1, b'x'*65536)")
        started = time.monotonic()
        with patch('nlc.rtf_receipt.subprocess.Popen', side_effect=start):
            with self.assertRaisesRegex(_ReadbackPending, 'byte budget'):
                adapter._decode(b'{\\rtf1 Hello}')
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdin.closed)
        self.assertTrue(children[0].stdout.closed)

    def test_decoder_drains_output_while_feeding_large_input_and_accepts_exact_limit(self):
        adapter = self.receipt(timeout_seconds=3)
        adapter._MAX_TEXT_BYTES = 65536
        adapter._decode_deadline = adapter._clock() + 3
        # Writing before reading deliberately fills the stdout pipe. Sequential
        # input/output handling deadlocks on the much larger stdin payload.
        start, children = self.fake_decoder(
            "import sys\nsys.stdout.buffer.write(b'x'*65536)\n"
            "sys.stdout.buffer.flush()\nraw=sys.stdin.buffer.read()\n"
            "sys.exit(0 if len(raw)==1048576 else 1)")
        raw = b'{\\rtf1 ' + b'a' * (1048576 - 8) + b'}'
        with patch('nlc.rtf_receipt.subprocess.Popen', side_effect=start):
            self.assertEqual(adapter._decode(raw), 'x' * 65536)
        self.assertEqual(children[0].returncode, 0)

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX process-group cleanup')
    def test_decoder_exited_parent_with_descendant_pipe_times_out_and_reaps(self):
        adapter = self.receipt(timeout_seconds=.15)
        adapter._decode_deadline = adapter._clock() + .15
        child_path = self.directory / 'decoder-child.pid'
        start, children = self.fake_decoder(
            "import os,time\nif os.fork()==0:\n"
            f" with open({str(child_path)!r}, 'w') as f: f.write(str(os.getpid()))\n"
            " time.sleep(20)\n"
            "else: os._exit(0)")
        def exited_parent(argv, **kwargs):
            proc = start(argv, **kwargs)
            # Establish the condition under test before the readback budget:
            # interpreter startup (especially with tracemalloc) is not the
            # exited-parent/retained-descendant-pipe condition.
            proc.wait(timeout=3)
            ready_deadline = time.monotonic() + 3
            while not child_path.exists():
                if time.monotonic() >= ready_deadline:
                    raise AssertionError('decoder descendant did not start')
                time.sleep(.005)
            adapter._decode_deadline = adapter._clock() + .15
            return proc
        started = time.monotonic()
        with patch('nlc.rtf_receipt.subprocess.Popen', side_effect=exited_parent):
            with self.assertRaisesRegex(_ReadbackPending, 'deadline'):
                adapter._decode(b'{\\rtf1 Hello}')
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(children[0].returncode, 0)
        descendant = int(child_path.read_text())
        # An exited orphan can briefly remain a zombie until the OS reaps it;
        # it must not keep running after the group's deadline cleanup.
        status = subprocess.run(['/bin/ps', '-o', 'stat=', '-p', str(descendant)],
                                capture_output=True, text=True, timeout=1)
        self.assertTrue(not status.stdout.strip() or
                        status.stdout.strip().startswith('Z'), status.stdout)

    def test_decoder_interruption_closes_pipes_and_reaps(self):
        adapter = self.receipt(timeout_seconds=2)
        adapter._decode_deadline = adapter._clock() + 2
        start, children = self.fake_decoder('import time;time.sleep(20)')
        with patch('nlc.rtf_receipt.subprocess.Popen', side_effect=start), \
                patch('nlc.rtf_receipt.selectors.DefaultSelector.select',
                      side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                adapter._decode(b'{\\rtf1 Hello}')
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdin.closed)
        self.assertTrue(children[0].stdout.closed)

    def xattr(self, value):
        name='com.nlc.receipt-test' if sys.platform=='darwin' else 'user.nlc_receipt_test'
        data=str(value).encode()
        if hasattr(os, 'setxattr'):
            os.setxattr(self.path,name,data)
        elif sys.platform=='darwin':
            # CPython on this macOS build omits os.setxattr. Exercise the same
            # real filesystem change through the native libc entry point.
            import ctypes
            libc=ctypes.CDLL(None,use_errno=True)
            call=libc.setxattr
            call.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,
                           ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int]
            call.restype=ctypes.c_int
            buffer=ctypes.create_string_buffer(data)
            if call(os.fsencode(self.path),name.encode(),buffer,len(data),0,0)!=0:
                raise OSError(ctypes.get_errno(),'setxattr failed')
        else:
            self.skipTest('filesystem xattrs not supported')

    @unittest.skipUnless(Path('/usr/bin/textutil').is_file(),'macOS textutil decoder')
    def test_ctime_only_xattr_during_decode_retries_whole_readback(self):
        adapter=self.receipt(timeout_seconds=1)
        context=adapter.prepare_commit(self.event)
        raw=b'{\\rtf1\\ansi Hello}'
        self.path.write_bytes(raw)
        original=adapter._decode
        decoded=[]
        def decode(value):
            decoded.append(value)
            text=original(value)
            if len(decoded)==1:self.xattr('one update')
            return text
        with patch.object(adapter,'_decode',side_effect=decode):
            proof=adapter.after_commit(self.event,self.response,context)
        self.assertIsNotNone(proof)
        self.assertEqual([raw,raw],decoded)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),proof.evidence.sha256)

    def test_ctime_only_during_read_and_parent_final_check_retries(self):
        for boundary in ('read','parent-final'):
            with self.subTest(boundary=boundary):
                if self.path.exists():self.path.unlink()
                adapter=self.receipt(timeout_seconds=1)
                context=adapter.prepare_commit(self.event)
                self.path.write_bytes(b'{\\rtf1 Hello}')
                reads=[]
                original_read=adapter._read_file
                def readback(fd):
                    reads.append(fd)
                    result=original_read(fd)
                    if boundary=='parent-final' and len(reads)==1:self.xattr('after-return')
                    return result
                original_os_read=os.read
                changed=[]
                def read(fd,n):
                    value=original_os_read(fd,n)
                    if boundary=='read' and not changed:
                        changed.append(True);self.xattr('during-read')
                    return value
                with patch.object(adapter,'_decode',return_value='Hello'), \
                     patch.object(adapter,'_read_file',side_effect=readback), \
                     patch('nlc.rtf_receipt.os.read',side_effect=read):
                    proof=adapter.after_commit(self.event,self.response,context)
                self.assertIsNotNone(proof)
                self.assertEqual(2,len(reads))

    def test_continuous_ctime_churn_never_issues_receipt(self):
        adapter=self.receipt(timeout_seconds=.12)
        context=adapter.prepare_commit(self.event)
        self.path.write_bytes(b'{\\rtf1 Hello}')
        count=[0]
        def churn(raw):
            count[0]+=1;self.xattr(count[0])
            return 'Hello'
        with patch.object(adapter,'_decode',side_effect=churn):
            self.assertIsNone(adapter.after_commit(self.event,self.response,context))
        self.assertGreater(count[0],1)

    def test_non_ctime_identity_changes_still_raise_and_plain_receipt_stays_strict(self):
        from nlc.artifact_receipt import FreshFileReceipt
        identity=(1,2,0o100644,501,1,20,100,20,200)
        for field in range(len(identity)-1):
            changed=list(identity);changed[field]+=1
            with self.subTest(field=field),self.assertRaises(ArtifactReceiptError):
                FreshRTFReceipt._require_stable(identity,tuple(changed))
        with self.assertRaises(ArtifactReceiptError):
            FreshFileReceipt._final_readback_matches(None,(1,2,3),(1,2,4))


if __name__=='__main__':unittest.main()
