import unittest
from tacto.flow import replay
from tacto.library import textedit_save
from tacto.artifact_receipt import ArtifactReceiptError
from tacto.existing_file_receipt import _refuse
class ReceiptDiagnostics(unittest.TestCase):
    def test_after_commit_discloses_only_closed_receipt_codes(self):
        class App:
            capabilities={'action.menu.focused_document'}
            def observe(self,*a,**k):return {'type':'observation','obs_id':'o','tree':'0 Window  Title: note.txt [focused]'}
            def act(self,*a,**k):return {'type':'acted'}
            def wait_until(self,*a,**k):return self.observe()
        flow=textedit_save().bind(document='/tmp/a/note.txt',name='note.txt')
        for code,expected in [('text_unavailable','text_unavailable'),([], 'host_commit_evidence_failed'),('PRIVATE_SENTINEL','host_commit_evidence_failed')]:
            def after(*a):
                error=ArtifactReceiptError('PRIVATE_SENTINEL')
                error.public_code=code
                raise error
            result=replay(App(),flow,approvals={0:True},after_commit=after)
            text=str(result.log)
            self.assertIn(expected,text)
            self.assertIn('commit_unverified',text)
            self.assertNotIn('PRIVATE_SENTINEL',text)
