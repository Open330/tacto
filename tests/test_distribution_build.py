"""A distribution must name a version and one unmodified committed source."""
import importlib.util
import json
import os
import shutil
from unittest import mock
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'setup'))
from build_distribution import release_version,committed_sources,require_timestamps
import build_standalone_preview as preview
import build_distribution as distribution

class DistributionBuildTests(unittest.TestCase):
    def test_release_tags_are_closed(self):
        self.assertEqual(('0.1.0','15'),release_version('v0.1.0-preview.15'))
        self.assertEqual(('1.0.0',None),release_version('v1.0.0'))
        for tag in ('main','../v0.1.0','v0.1.0-preview.0','v0.1.0-preview.01','v0.1.0; echo bad'):
            with self.subTest(tag=tag),self.assertRaises(ValueError):release_version(tag)
    def test_dirty_or_untracked_sources_cannot_claim_a_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def git(*args):return subprocess.run(['git','-C',folder,*args],check=True,capture_output=True)
            git('init');git('config','user.name','Test');git('config','user.email','test@example.invalid')
            (root/'tacto').mkdir();source=root/'tacto/first_run.py';source.write_text('version = 1\n')
            git('add','.');git('commit','-m','fixture')
            before=committed_sources(root)
            self.assertIn('tacto/first_run.py',before['files'])
            source.write_text('version = 2\n')
            with self.assertRaises(RuntimeError):committed_sources(root)
            git('checkout','--','.');(root/'untracked').write_text('new')
            with self.assertRaises(RuntimeError):committed_sources(root)
            (root/'untracked').unlink()
            self.assertEqual(before,committed_sources(root))

    def test_every_packaged_macho_needs_a_timestamp_not_only_the_venv(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('Contents/MacOS/TactoSetup','Contents/Resources/runtime/.venv/bin/python3',
                         'Contents/Resources/runtime/extra/tool'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(next(iter(preview.MAGICS))+b'\0'*16)
            (root/'Contents/Info.plist').write_text('<plist/>')
            found=list(preview.native_files(root))
            self.assertEqual(3,len(found))
            class Signed:
                def __init__(self,stderr):self.stderr=stderr
            def run(arguments,**_):
                return Signed('Authority=x\n' if arguments[-1].endswith('extra/tool') else 'Timestamp=Oct 21, 2026\n')
            with self.assertRaisesRegex(RuntimeError,'tool'):require_timestamps(found,run=run)
            signed=[p for p in found if p.name!='tool']
            self.assertEqual(2,require_timestamps(signed,run=lambda a,**_:Signed('Timestamp=now\n')))



class DistributionInventoryTests(unittest.TestCase):
    def inventory(self):
        return {'schema':1,'distributions':{'tacto-runtime':'0.1.0','playwright':'1.63.0'},
                'native_paths':['Contents/MacOS/TactoSetup','Contents/Resources/runtime/native.so']}

    def package(self,venv,name='playwright',version='1.63.0',directory=None):
        site=Path(venv)/'lib/python3.11/site-packages'
        metadata=site/(directory or (name+'-'+version+'.dist-info'))
        metadata.mkdir(parents=True)
        (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: '+name+'\nVersion: '+version+'\n')

    def test_metadata_comes_from_packaged_environment_and_normalizes_names(self):
        with tempfile.TemporaryDirectory() as folder:
            self.package(folder,'PyObjC_Framework.ScreenCaptureKit','12.2')
            self.assertEqual({'pyobjc-framework-screencapturekit':'12.2'},distribution.distribution_packages(folder))

    def test_duplicate_or_missing_package_metadata_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            self.package(folder,'example_pkg','1');self.package(folder,'Example-Pkg','2')
            with self.assertRaisesRegex(RuntimeError,'duplicate'):
                distribution.distribution_packages(folder)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError,'site-packages'):
                distribution.distribution_packages(folder)

    def test_all_extras_check_pins_packaged_environment_without_installing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);python=root/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
            self.package(root/'.venv')
            run=mock.Mock(return_value=subprocess.CompletedProcess([],0,'',''))
            self.assertEqual({'playwright':'1.63.0'},distribution.require_release_environment(root,run))
            args,kwargs=run.call_args
            for flag in ('--frozen','--all-extras','--check','--inexact','--offline'):
                self.assertIn(flag,args[0])
            self.assertEqual(str((root/'.venv').resolve()),kwargs['env']['UV_PROJECT_ENVIRONMENT'])
            self.assertEqual(root,kwargs['cwd'])
            self.assertEqual(['uv','lock','--check','--offline'],run.call_args_list[0].args[0])
            self.assertNotIn('UV_FROZEN',kwargs['env'])

    def test_missing_extras_fail_before_any_copy_or_signing(self):
        with mock.patch.object(distribution,'committed_sources',return_value={}), \
             mock.patch.object(distribution,'require_release_environment',side_effect=RuntimeError('missing locked extras')), \
             mock.patch.object(distribution.preview,'build') as builder, \
             mock.patch.object(distribution.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'extras'):
                distribution.build('unused.app','unused','v0.1.0-preview.19')
            builder.assert_not_called();run.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            python=Path(folder)/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
            run=mock.Mock(side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],1,'','missing playwright')])
            with self.assertRaisesRegex(RuntimeError,'uv sync --frozen --all-extras'):
                distribution.require_release_environment(Path(folder),run)

    def test_package_deletion_still_fails_before_final_timestamp_signing(self):
        packaged=self.inventory();del packaged['distributions']['playwright']
        with mock.patch.object(distribution,'committed_sources',return_value={}), \
             mock.patch.object(distribution,'require_release_environment',return_value={'tacto-runtime':'0.1.0'}), \
             mock.patch.object(distribution,'read_baseline',return_value=self.inventory()), \
             mock.patch.object(distribution.preview,'build',return_value={}) as builder, \
             mock.patch.object(distribution,'app_inventory',return_value=packaged), \
             mock.patch.object(distribution.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'playwright'):
                distribution.build('unused.app','unused','v0.1.0-preview.19')
            builder.assert_called_once()
            self.assertEqual(2,run.call_count)
            self.assertTrue(all(call.args[0][0]=='bash' for call in run.call_args_list))

    def test_removed_package_and_native_path_each_require_explicit_reason(self):
        previous=self.inventory();current=self.inventory()
        del current['distributions']['playwright'];current['native_paths'].pop()
        none={'distributions':{},'native_paths':{}}
        with self.assertRaisesRegex(RuntimeError,'distributions'):
            distribution.require_inventory_compatibility(current,previous,none)
        packages={'distributions':{'playwright':'retired backend'},'native_paths':{}}
        with self.assertRaisesRegex(RuntimeError,'native_paths'):
            distribution.require_inventory_compatibility(current,previous,packages)
        allowed={'distributions':{'playwright':'retired backend'},
                 'native_paths':{'Contents/Resources/runtime/native.so':'removed with backend'}}
        result=distribution.require_inventory_compatibility(current,previous,allowed)
        self.assertTrue(result['passed']);self.assertEqual(['playwright'],result['removed_distributions'])
        self.assertEqual(64,len(result['baseline_inventory_sha256']))

    def test_version_updates_and_added_paths_do_not_look_like_removals(self):
        previous=self.inventory();current=self.inventory()
        current['distributions']['playwright']='1.64.0';current['distributions']['new-lib']='1'
        current['native_paths'].append('Contents/new-native')
        result=distribution.require_inventory_compatibility(current,previous,{'distributions':{},'native_paths':{}})
        self.assertTrue(result['passed']);self.assertFalse(result['removed_native_paths'])

    def test_stale_allowlist_and_wildcards_do_not_silently_accumulate(self):
        previous=self.inventory()
        with self.assertRaisesRegex(ValueError,'stale'):
            distribution.require_inventory_compatibility(previous,previous,{'distributions':{'old-package':'old exception'},'native_paths':{}})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'allow.json'
            for value in ({'distributions':{'*':'all'},'native_paths':{}},
                          {'distributions':{'playwright':''},'native_paths':{}}):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):distribution.read_removal_allowlist(path)

    def test_baseline_manifest_requires_real_inventory_and_initial_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'SOURCE-MANIFEST.json'
            path.write_text(json.dumps({'files':{},'workspace_commit':'old'}))
            with self.assertRaises(ValueError):distribution.read_baseline(path)
            path.write_text(json.dumps({'distribution_inventory':self.inventory()}))
            self.assertEqual(self.inventory(),distribution.read_baseline(path))
            with self.assertRaises(RuntimeError):distribution.read_baseline(Path(folder)/'absent.app')
            self.assertIsNone(distribution.read_baseline(initial_distribution=True))
            with self.assertRaises(ValueError):distribution.read_baseline(path,initial_distribution=True)

    def test_old_app_without_embedded_inventory_is_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'previous.app';venv=app/'Contents/Resources/runtime/.venv'
            self.package(venv);binary=app/'Contents/MacOS/TactoSetup';binary.parent.mkdir(parents=True)
            binary.write_bytes(next(iter(preview.MAGICS))+b'\0'*16)
            found=distribution.read_baseline(app)
            self.assertEqual({'playwright':'1.63.0'},found['distributions'])
            self.assertEqual(['Contents/MacOS/TactoSetup'],found['native_paths'])

    def test_invalid_or_empty_inventory_never_proves_compatibility(self):
        for native in ([],['../outside'],['/absolute'],['a/../b'],['x','x'],[{'x':1}]):
            bad=self.inventory();bad['native_paths']=native
            with self.subTest(native=native),self.assertRaises(ValueError):distribution.validate_inventory(bad)
        bad=self.inventory();bad['distributions']={}
        with self.assertRaises(ValueError):distribution.validate_inventory(bad)


    def test_copy_loss_and_native_deletion_stop_before_timestamp_signing(self):
        previous=self.inventory()
        for kind in ('package','native'):
            packaged=self.inventory()
            if kind=='package':del packaged['distributions']['playwright']
            else:packaged['native_paths'].pop()
            with self.subTest(kind=kind), \
                 mock.patch.object(distribution,'committed_sources',return_value={}), \
                 mock.patch.object(distribution,'require_release_environment',return_value=previous['distributions']), \
                 mock.patch.object(distribution,'read_baseline',return_value=previous), \
                 mock.patch.object(distribution,'app_inventory',return_value=packaged), \
                 mock.patch.object(distribution.preview,'build',return_value={}) as builder, \
                 mock.patch.object(distribution.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'packaged distributions|native_paths'):
                    distribution.build('unused.app','unused','v0.1.0-preview.19')
                builder.assert_called_once()
                self.assertEqual(2,run.call_count)
                self.assertTrue(all(call.args[0][0]=='bash' for call in run.call_args_list))

    def test_base_python_packages_preserved_outside_venv_do_not_look_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'result.app'
            (root/'tacto').mkdir();(root/'tacto/__init__.py').write_text('x = 1\n')
            for name in ('README.md','README.ko.md'):(root/name).write_text('guide')
            before={'schema':1,'workspace_commit':'a'*40,'files':{},'digest':'b'*64}
            def fake_build(app,identity):
                runtime=app/'Contents/Resources/runtime'
                self.package(runtime/'.venv')
                self.package(runtime/'.venv','pip','26.2.1')
                self.package(runtime/'.venv','setuptools','84.0.0')
                site=runtime/'.venv/lib/python3.11/site-packages/tacto';site.mkdir()
                (site/'__init__.py').write_text('x = 1\n')
                for name in ('Contents/MacOS/TactoSetup','Contents/Resources/runtime/.venv/bin/python3',
                             'Contents/Resources/runtime/launcher/tacto',
                             'Contents/Resources/runtime/helper/Tacto Helper.app/Contents/MacOS/TactoHelper'):
                    path=app/name;path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(next(iter(preview.MAGICS))+b'\0'*16)
                (app/'Contents/Info.plist').write_bytes(distribution.plistlib.dumps({}))
                return {'mock_build':True}
            def fake_run(args,**kwargs):
                if args[0]=='codesign':
                    sealed=json.loads((output/'Contents/Resources/first-run-source.json').read_text())
                    self.assertIn('distribution_inventory',sealed)
                    self.assertTrue(sealed['inventory_compatibility']['passed'])
                return subprocess.CompletedProcess(args,0,'','Timestamp=fixture\n')
            timestamps=distribution.require_timestamps
            prior={'schema':1,'distributions':{'playwright':'1.63.0','pip':'26.2.1','setuptools':'84.0.0'},
                   'native_paths':['Contents/MacOS/TactoSetup','Contents/Resources/runtime/.venv/bin/python3',
                                   'Contents/Resources/runtime/launcher/tacto',
                                   'Contents/Resources/runtime/helper/Tacto Helper.app/Contents/MacOS/TactoHelper']}
            with mock.patch.object(distribution,'read_baseline',return_value=prior), \
                 mock.patch.object(distribution,'ROOT',root), \
                 mock.patch.object(distribution,'committed_sources',return_value=before), \
                 mock.patch.object(distribution,'require_release_environment',return_value={'playwright':'1.63.0'}), \
                 mock.patch.object(distribution.preview,'build',side_effect=fake_build), \
                 mock.patch.object(distribution.preview,'requirement',return_value='fixture'), \
                 mock.patch.object(distribution.subprocess,'run',side_effect=fake_run), \
                 mock.patch.object(distribution,'require_timestamps',side_effect=lambda paths:timestamps(paths,run=fake_run)):
                report=distribution.build(output,'fixture','v0.1.0-preview.19')
            sealed=json.loads((output/'Contents/Resources/first-run-source.json').read_text())
            self.assertEqual(report['distribution_inventory'],sealed['distribution_inventory'])
            self.assertEqual(prior['distributions'],sealed['distribution_inventory']['distributions'])
            self.assertEqual(4,len(sealed['distribution_inventory']['native_paths']))
            self.assertTrue(report['all_extras_verified'])
            self.assertEqual('previous_release',sealed['inventory_compatibility']['baseline'])


    @unittest.skipUnless(shutil.which('uv'),'uv required for real lock freshness regression')
    def test_new_pyproject_extra_cannot_hide_behind_a_frozen_stale_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);project=root/'pyproject.toml'
            environment=dict(os.environ,UV_PROJECT_ENVIRONMENT=str(root/'.venv'))
            environment.pop('UV_FROZEN',None);environment.pop('UV_LOCKED',None)
            project.write_text('[project]\nname="inventory-lock-fixture"\nversion="0.0.0"\nrequires-python=">=3.11"\n[tool.uv]\npackage=false\n')
            for args in (['uv','lock','--offline'],['uv','sync','--frozen','--all-extras','--offline']):
                subprocess.run(args,cwd=root,env=environment,check=True,capture_output=True,text=True,timeout=30)
            original_lock=(root/'uv.lock').read_bytes()
            project.write_text(project.read_text()+'[project.optional-dependencies]\nweb=["requests==2.34.2"]\n')
            # The former frozen-only check says nothing is missing: it has not
            # read the newly declared extra into the locked dependency graph.
            frozen=subprocess.run(['uv','sync','--frozen','--all-extras','--check','--inexact','--offline'],
                                  cwd=root,env=environment,capture_output=True,text=True,timeout=30)
            self.assertEqual(0,frozen.returncode,frozen.stderr)
            with self.assertRaisesRegex(RuntimeError,'lockfile'):
                distribution.require_release_environment(root)
            self.assertEqual(original_lock,(root/'uv.lock').read_bytes())


    def test_selected_baseline_identity_is_auditable_but_not_publication_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'SOURCE-MANIFEST.json'
            path.write_text(json.dumps({'distribution_inventory':self.inventory(),
                                        'release_tag':'v0.1.0-preview.18','workspace_commit':'a'*40}))
            previous=distribution.read_baseline(path)
            report=distribution.require_inventory_compatibility(self.inventory(),previous,
                                                                {'distributions':{},'native_paths':{}})
            self.assertEqual({'release_tag':'v0.1.0-preview.18','source_commit':'a'*40,
                              'publication_verified':False},report['baseline_identity'])
        self.assertEqual({'release_tag':None,'source_commit':None,'publication_verified':False},
                         distribution.baseline_identity({'TactoReleaseTag':'private path','TactoSourceCommit':'invalid'}))

    def test_base_prefix_package_missing_from_final_app_still_fails(self):
        previous=self.inventory();previous['distributions'].update(pip='26.2.1',setuptools='84.0.0')
        for missing in ('pip','setuptools'):
            current={'schema':1,'distributions':dict(previous['distributions']),
                     'native_paths':list(previous['native_paths'])}
            del current['distributions'][missing]
            with self.subTest(missing=missing), \
                 mock.patch.object(distribution,'committed_sources',return_value={}), \
                 mock.patch.object(distribution,'require_release_environment',return_value=self.inventory()['distributions']), \
                 mock.patch.object(distribution,'read_baseline',return_value=previous), \
                 mock.patch.object(distribution.preview,'build',return_value={}), \
                 mock.patch.object(distribution,'app_inventory',return_value=current), \
                 mock.patch.object(distribution.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,missing):
                    distribution.build('unused.app','fixture','v0.1.0-preview.19')
                self.assertEqual(2,run.call_count)
                self.assertTrue(all(call.args[0][0]=='bash' for call in run.call_args_list))
