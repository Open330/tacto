"""An evaluator-owned TextEdit fixture, bound over one signed helper channel.

Never closes a user document. Errors leave the owned artifact in place. The
lifecycle/readback checks are observations, not an atomic lease on the app/FS.
"""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unicodedata

TARGET = 'com.apple.TextEdit'
CAPS = {'evidence.document_text.path_selector','observe.document_token',
        'evidence.document_window_status','action.click', 'observe.subrole', 'expect.attribute.subrole'}


class SignedOwnedTextEdit:
    def __init__(self, driver, name):
        if not CAPS <= set(getattr(driver, 'capabilities', ())):
            raise RuntimeError('signed_owned_document_capability_unavailable')
        if not isinstance(name,str) or Path(name).name != name or name in ('','.', '..'):
            raise ValueError('invalid fixture filename')
        self.driver, self.name = driver, name
        self.path = self.directory = self.directory_identity = self.token = None
        self.prepared = False
        self.setup_started = False
        self.prepared_file_identity = None
        self.initial_empty_text_verified = False
        self.initial_expected_text_verified = False
        self.close_requested = False
        self.closed_observed = False
        self.status_observations = []
        self.status_observations_truncated = False

    def prepare(self):
        if self.directory is not None:
            raise RuntimeError('owned_fixture_already_created')
        self.directory = Path(tempfile.mkdtemp(prefix='tacto-signed-owned-')).resolve()
        st=self.directory.lstat()
        self.directory_identity=(st.st_dev,st.st_ino)
        self.path=self.directory/self.name
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.close(fd)
        file=self.path.lstat()
        self.prepared_file_identity=(file.st_dev,file.st_ino)
        self.prepared=True

    def setup(self):
        if self.setup_started:
            raise RuntimeError('owned_fixture_setup_already_started')
        if self.directory is None:self.prepare()
        if not self.prepared:raise RuntimeError('owned_fixture_already_created')
        # Revalidate the separately prepared artifact immediately before UI
        # bootstrap. This is not atomic protection against a same-user writer.
        directory=self.directory.lstat();file=self.path.lstat()
        if (self.directory.resolve()!=self.directory or not stat.S_ISDIR(directory.st_mode)
                or (directory.st_dev,directory.st_ino)!=self.directory_identity
                or directory.st_uid!=os.getuid() or stat.S_IMODE(directory.st_mode)!=0o700
                or os.listdir(self.directory)!=[self.name]
                or not stat.S_ISREG(file.st_mode) or file.st_uid!=os.getuid()
                or file.st_nlink!=1 or stat.S_IMODE(file.st_mode)!=0o600
                or (file.st_dev,file.st_ino)!=self.prepared_file_identity or file.st_size!=0):
            raise RuntimeError('owned_prepared_artifact_changed')
        self.setup_started=True  # A lost open/pin reply never permits reopening.
        # Fixture bootstrap only; all observation/close actions use the helper.
        proc=subprocess.run(['open','-g','-a','TextEdit',str(self.path)],
                            capture_output=True,timeout=20)
        if proc.returncode != 0:
            raise RuntimeError('owned_fixture_open_failed')
        self._pin()

    def resume(self, directory, expected_identity, expected_text=""):
        if self.directory is not None:
            raise RuntimeError("owned_fixture_already_created")
        self._text_budget(expected_text)
        # Only a trusted host may supply the identity of its prior fixture.
        # A matching name/prefix alone does not prove creator ownership.
        directory=Path(directory)
        st=directory.lstat()
        if (not directory.is_absolute() or directory.resolve()!=directory
                or not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid()
                or stat.S_IMODE(st.st_mode)!=0o700
                or (st.st_dev,st.st_ino)!=tuple(expected_identity)
                or os.listdir(directory)!=[self.name]):
            raise RuntimeError('owned_fixture_resume_identity_unavailable')
        self.setup_started=True
        self.directory,self.directory_identity=directory,tuple(expected_identity)
        self.path=directory/self.name
        self._pin(expected_text)

    @staticmethod
    def _text_budget(expected_text):
        if not isinstance(expected_text,str):raise ValueError('invalid expected fixture text')
        try:units=len(expected_text.encode('utf-16-le'))//2
        except UnicodeError:raise ValueError('invalid expected fixture text') from None
        if units>1_000_000:raise ValueError('expected fixture text exceeds pin budget')
        return units

    def _matches_path(self, value):
        if not isinstance(value,str) or unicodedata.normalize('NFC',value)!=unicodedata.normalize('NFC',str(self.path)):
            return False
        try:
            named,owned=os.lstat(value),self.path.lstat()
            return (stat.S_ISREG(named.st_mode) and stat.S_ISREG(owned.st_mode)
                    and (named.st_dev,named.st_ino)==(owned.st_dev,owned.st_ino))
        except OSError:
            return False

    def _pin(self, expected_text=""):
        units=self._text_budget(expected_text)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            reply=self.driver.document_text(TARGET,document_path=str(self.path),max_utf16_units=units)
            if (type(reply) is dict and reply.get('type')=='document_text'
                    and self._matches_path(reply.get('document_path'))
                    and reply.get('text_complete') is True and reply.get('text')==expected_text
                    and isinstance(reply.get('document_token'),str)
                    and 0<len(reply['document_token'])<=128):
                self.token=reply['document_token']
                self.initial_empty_text_verified=expected_text==''
                self.initial_expected_text_verified=True
                return
            time.sleep(.1)
        raise RuntimeError('owned_fixture_pin_unavailable')

    def read(self):
        if self.token is None:
            return None
        reply=self.driver.document_text(TARGET,self.token)
        if (type(reply) is dict and reply.get('type')=='document_text'
                and reply.get('document_token')==self.token
                and self._matches_path(reply.get('document_path'))
                and reply.get('text_complete') is True and isinstance(reply.get('text'),str)):
            return reply['text']
        return None

    def write(self, text):
        from tacto.flow import Selector
        if not isinstance(text,str) or 'action.set_value' not in self.driver.capabilities:
            raise RuntimeError('owned_fixture_write_unavailable')
        obs=self.driver.observe(TARGET,document_token=self.token)
        if obs.get('type')!='observation' or obs.get('document_token')!=self.token or obs.get('truncated') is not False:
            raise RuntimeError('owned_fixture_write_observation_unavailable')
        index,_=Selector(role='TextArea',identifier='First Text View').resolve(obs['tree'])
        result=self.driver.act(obs['obs_id'],TARGET,{'name':'set_value','element_index':index,'value':text},
            expect={'role':'TextArea','attribute':'Identifier','label':'First Text View','enabled':True},
            risk_class='preapproved',approved=True,intent_text='Edit only the evaluator-created temporary document')
        if result.get('type')!='acted':
            raise RuntimeError('owned_fixture_write_unverified')
        return self.read()==text

    def status(self):
        if self.token is None:
            return None
        reply=self.driver.document_window_status(TARGET,self.token)
        from tacto.helper import DOCUMENT_WINDOW_STATUS_REASONS
        valid=(type(reply) is dict and reply.get('type')=='document_window_status'
               and reply.get('document_token')==self.token
               and reply.get('same_process_verified') is True
               and reply.get('state') in ('open','closed'))
        state=reply['state'] if valid else None
        if len(self.status_observations)<64:
            reason=reply.get('reason_code') if type(reply) is dict else None
            self.status_observations.append({'state':state,
                'reason_code':reason if isinstance(reason,str) and reason in DOCUMENT_WINDOW_STATUS_REASONS else None})
        else:
            self.status_observations_truncated=True
        return state

    def _remove_artifact(self):
        # Anchor file unlink to the original directory fd; never recursively
        # delete a directory or follow a replacement/symlink. The final rmdir
        # identity check is not atomic against a same-user directory rename.
        try:
            if self.directory.resolve()!=self.directory:
                return 'artifact_changed_left_as_is'
            fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                st=os.fstat(fd)
                if ((st.st_dev,st.st_ino)!=self.directory_identity or st.st_uid!=os.getuid()
                        or stat.S_IMODE(st.st_mode)!=0o700 or os.listdir(fd)!=[self.name]):
                    return 'artifact_changed_left_as_is'
                file=os.stat(self.name,dir_fd=fd,follow_symlinks=False)
                if not stat.S_ISREG(file.st_mode) or file.st_uid!=os.getuid() or file.st_nlink!=1:
                    return 'artifact_changed_left_as_is'
                os.unlink(self.name,dir_fd=fd)
                named=self.directory.lstat()
                if (named.st_dev,named.st_ino)!=self.directory_identity:
                    return 'file_removed_directory_changed_left_as_is'
                os.rmdir(self.directory)
            finally:
                os.close(fd)
        except OSError:
            return 'artifact_cleanup_failed_left_as_is'
        return 'closed'

    def cleanup(self):
        from tacto.flow import Selector
        state=self.status()
        if state is None:
            return 'document_status_unavailable_left_as_is'
        if state=='closed':
            self.closed_observed=True
            return self._remove_artifact()
        if self.close_requested:
            return 'close_pending_left_as_is'
        obs=self.driver.observe(TARGET,document_token=self.token)
        if (type(obs) is not dict or obs.get('type')!='observation'
                or obs.get('document_token')!=self.token or obs.get('truncated') is not False):
            return 'document_observation_unavailable_left_as_is'
        try:
            matches=[int(line.strip().split()[0]) for line in obs['tree'].splitlines()
                     if Selector(role='Button').matches(line)
                     and Selector._attr(line,'Subrole')=='AXCloseButton'
                     and '[disabled]' not in line]
            if not matches:
                index,_=Selector(role='Button',desc=['close button','닫기 버튼']).resolve(obs['tree'])
            elif len(matches)==1:
                index=matches[0]
            else:
                raise LookupError
        except (LookupError,KeyError,TypeError):
            return 'close_control_unavailable_left_as_is'
        self.close_requested=True  # Unknown effect must never cause a second close.
        reply=self.driver.act(obs['obs_id'],TARGET,{'name':'click','element_index':index},
                              expect={'role':'Button','attribute':'Subrole','label':'AXCloseButton','enabled':True},
                              risk_class='preapproved',approved=True,
                              intent_text='Close only the evaluator-created temporary document')
        if type(reply) is not dict or reply.get('type')!='acted':
            return 'close_unverified_left_as_is'
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            state=self.status()
            if state=='closed':
                self.closed_observed=True
                return self._remove_artifact()
            # Read-only revalidation may outlive a transient closing-window
            # snapshot. Never repeat the close action to obtain another sample.
            time.sleep(.1)
        if state is None:
            return 'document_status_unavailable_left_as_is'
        # A dirty-document sheet may remain. Do not guess or discard it.
        return 'close_pending_left_as_is'
