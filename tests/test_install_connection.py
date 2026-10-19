"""Actual installer/records on isolated paths, including two runtime moves."""
import fcntl
import os
from pathlib import Path
import shutil
import shlex
import tempfile
import unittest
from unittest.mock import patch
from tacto.install_connection import connect

ROOT=Path(__file__).resolve().parents[1]


class InstallConnectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.folder=Path(temp.name).resolve();self.root=self.folder/'runtime';self.root.mkdir()
        (self.root/'skill').mkdir();(self.root/'launcher').mkdir()
        (self.root/'skill/SKILL.md').write_text('name: nlc\n')
        shutil.copyfile(ROOT/'skill/install.sh',self.root/'skill/install.sh')
        (self.root/'launcher/tacto').write_text('#!/bin/sh\nexit 0\n');(self.root/'launcher/tacto').chmod(0o700)
        (self.root/'.venv/bin').mkdir(parents=True)
        python=self.root/'.venv/bin/python'
        python.write_text('#!/bin/sh\nexec '+shlex.quote(str(ROOT/'.venv/bin/python'))+' "$@"\n')
        python.chmod(0o700)
        self.state=self.folder/'state';self.bin=self.folder/'bin';self.skills=self.folder/'skills'

    def connect(self):
        return connect(self.root,self.state,bin_dir=self.bin,skill_dirs=[self.skills])

    def test_two_moves_select_matching_record_and_recheck_actual_links(self):
        for name in ('first','second','third'):
            result=self.connect();self.assertEqual('connected',result['status'],result)
            self.assertTrue(result['checked']);self.assertTrue(result['record_written'])
            self.assertEqual(str(self.root/'launcher/tacto'),os.readlink(self.bin/'tacto'))
            self.assertEqual(str(self.root/'skill'),os.readlink(self.skills/'tacto'))
            moved=self.folder/name;self.root.rename(moved);self.root=moved
        self.assertEqual(3,len(list(self.state.glob('*.json'))))
        for record in self.state.glob('*.json'):self.assertEqual(0o600,record.stat().st_mode&0o777)

    def test_moved_runtime_repairs_recorded_skill_when_command_link_was_removed(self):
        self.assertEqual('connected',self.connect()['status'])
        (self.bin/'tacto').unlink()
        moved=self.folder/'moved runtime';self.root.rename(moved);self.root=moved
        result=self.connect()
        self.assertEqual('connected',result['status'],result)
        self.assertTrue(result['checked'])
        self.assertEqual(str(self.root/'launcher/tacto'),os.readlink(self.bin/'tacto'))
        self.assertEqual(str(self.root/'skill'),os.readlink(self.skills/'tacto'))

    def test_surviving_recorded_skill_does_not_authorize_foreign_command(self):
        self.assertEqual('connected',self.connect()['status'])
        moved=self.folder/'moved';self.root.rename(moved);self.root=moved
        (self.bin/'tacto').unlink();(self.bin/'tacto').symlink_to('/foreign/missing')
        result=self.connect()
        self.assertEqual('installation_failed',result['error'])
        self.assertEqual('/foreign/missing',os.readlink(self.bin/'tacto'))
        self.assertNotEqual(str(self.root/'skill'),os.readlink(self.skills/'tacto'))

    def test_foreign_command_and_state_are_not_adopted(self):
        self.bin.mkdir();(self.bin/'tacto').symlink_to('/foreign/missing')
        self.assertEqual('installation_failed',self.connect()['error'])
        self.assertEqual('/foreign/missing',os.readlink(self.bin/'tacto'))
        foreign=self.folder/'foreign';foreign.mkdir()
        link=self.folder/'linked-state';link.symlink_to(foreign)
        result=connect(self.root,link,bin_dir=self.bin,skill_dirs=[self.skills])
        self.assertEqual('invalid_state_directory',result['error']);self.assertEqual([],list(foreign.iterdir()))

    def test_busy_connection_refuses_before_install(self):
        self.state.mkdir(mode=0o700)
        fd=os.open(self.state/'connection.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual('connection_busy',self.connect()['error'])
            self.assertFalse(self.bin.exists())
        finally:os.close(fd)

    def test_repeated_connection_reuses_verified_record_without_rewriting_links(self):
        self.assertTrue(self.connect()['record_written'])
        links = [self.bin/'tacto', self.skills/'tacto']
        before = [(p.lstat().st_ino, p.lstat().st_mtime_ns) for p in links]
        records = {p: p.read_bytes() for p in self.state.glob('*.json')}
        from tacto.install_connection import run_install
        with patch('tacto.install_connection.run_install', wraps=run_install) as run:
            result = self.connect()
        self.assertEqual('connected', result['status'], result)
        self.assertTrue(result['checked']); self.assertTrue(result['record_reused'])
        self.assertFalse(result['record_written'])
        self.assertEqual(1, run.call_count)
        self.assertIn('--check', run.call_args.args[0])
        self.assertEqual(before, [(p.lstat().st_ino, p.lstat().st_mtime_ns) for p in links])
        self.assertEqual(records, {p: p.read_bytes() for p in self.state.glob('*.json')})

    def test_changed_destinations_and_changed_bytes_require_new_record(self):
        self.connect()
        other = self.folder/'other skills'
        result = connect(self.root,self.state,bin_dir=self.bin,skill_dirs=[other])
        self.assertTrue(result['record_written']);self.assertNotIn('record_reused',result)
        (self.root/'skill/SKILL.md').write_text('name: nlc\nupdated: true\n')
        result = connect(self.root,self.state,bin_dir=self.bin,skill_dirs=[other])
        self.assertTrue(result['record_written']);self.assertNotIn('record_reused',result)
        self.assertEqual(3,len(list(self.state.glob('*.json'))))

    def test_failed_read_only_check_does_not_report_reused_success(self):
        self.connect()
        with patch('tacto.install_connection.run_install',return_value=1):
            result = self.connect()
        self.assertEqual('failed',result['status'])
        self.assertNotIn('record_reused',result)

    def test_default_setup_destinations_reuse_only_after_actual_installer_check(self):
        home=self.folder/'isolated home'
        (home/'Library/Application Support/asx/profiles/example/skills').mkdir(parents=True)
        env={'HOME':str(home),'TACTO_BIN_DIR':'',
             'CODEX_HOME':str(home/'active codex'),'CLAUDE_CONFIG_DIR':str(home/'active claude')}
        with patch.dict(os.environ,env):
            first=connect(self.root,self.state)
            second=connect(self.root,self.state)
        self.assertEqual('connected',first['status'],first)
        self.assertTrue(first['record_written'])
        self.assertEqual('connected',second['status'],second)
        self.assertTrue(second['record_reused']);self.assertTrue(second['checked'])
        self.assertEqual(1,len(list(self.state.glob('*.json'))))
        self.assertEqual(str(self.root/'launcher/tacto'),os.readlink(home/'.local/bin/tacto'))

    def test_full_catalog_can_reuse_but_cannot_add_changed_installation(self):
        self.connect()
        record=next(self.state.glob('*.json')).read_bytes()
        for n in range(999):
            path=self.state/f'repeated-{n:04d}.json'
            with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:
                stream.write(record)
        result=self.connect()
        self.assertTrue(result['record_reused']);self.assertFalse(result['record_written'])
        (self.root/'skill/SKILL.md').write_text('name: nlc\nchanged: true\n')
        result=self.connect()
        self.assertEqual('record_catalog_full',result['error'])
        self.assertEqual(1000,len(list(self.state.glob('*.json'))))

    def test_returning_to_an_existing_destination_reuses_its_own_record(self):
        self.connect()
        other=self.folder/'other destination'
        self.assertTrue(connect(self.root,self.state,bin_dir=self.bin,skill_dirs=[other])['record_written'])
        result=self.connect()
        self.assertTrue(result['record_reused']);self.assertFalse(result['record_written'])
        self.assertEqual(2,len(list(self.state.glob('*.json'))))
