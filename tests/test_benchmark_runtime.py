"""Comparison preflight records what runs and fails before GUI/model work."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from examples.benchmark_runtime import runtime_evidence, guide_evidence, guide_runtime_compatibility, codex_config_evidence
from examples.benchmark_matched_conditions import main
from examples.repeat_benchmark import prompt


class BenchmarkRuntimeTests(unittest.TestCase):
    def runtime(self, root):
        for name in ['launcher/tacto','helper/Tacto Helper.app/Contents/MacOS/TactoHelper',
                     '.venv/bin/python','skill/SKILL.md',
                     '.venv/lib/python3.11/site-packages/tacto/__init__.py']:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('owned fixture');p.chmod(0o700)
        return root/'launcher/tacto'

    def test_config_evidence_tracks_the_inherited_profile_not_default_home(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile=root/'profile';profile.mkdir()
            config=profile/'config.toml';config.write_text('model="owned-example"\n')
            with patch.dict('os.environ',{'CODEX_HOME':str(profile)}):
                before=codex_config_evidence()
                self.assertEqual(str(config.resolve()),before['config_path'])
                self.assertEqual('CODEX_HOME',before['home_source'])
                self.assertEqual(hashlib.sha256(config.read_bytes()).hexdigest(),before['sha256'])
                self.assertNotIn('owned-example',json.dumps(before))
                config.write_text('model="changed"\n')
                self.assertNotEqual(before,codex_config_evidence())

    def test_config_absence_and_relative_home_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ',{'CODEX_HOME':tmp}):
                result=codex_config_evidence()
                self.assertFalse(result['exists']);self.assertIsNone(result['sha256'])
            with patch.dict('os.environ',{'CODEX_HOME':'relative-profile'}):
                with self.assertRaisesRegex(ValueError,'absolute'):
                    codex_config_evidence()

    def test_symlink_records_resolved_runtime_and_changed_executed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.runtime(root/'app');alias=root/'tacto';alias.symlink_to(entry)
            before=runtime_evidence(alias)
            self.assertEqual(str(entry.resolve()),before['resolved_launcher'])
            source=root/'app/.venv/lib/python3.11/site-packages/tacto/__init__.py'
            source.write_text('changed runtime')
            self.assertNotEqual(before['files'],runtime_evidence(alias)['files'])

    def test_missing_package_cannot_make_an_empty_success_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.runtime(root)
            package=root/'.venv/lib/python3.11/site-packages/tacto';package.rename(package.with_name('unrelated'))
            with self.assertRaisesRegex(ValueError,'package'):runtime_evidence(entry)

    def test_missing_helper_is_a_preflight_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.runtime(root)
            (root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper').unlink()
            with self.assertRaisesRegex(ValueError,'helper'):runtime_evidence(entry)

    def test_preflight_failure_does_not_start_fixture_or_agent(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('examples.benchmark_matched_conditions.Fixture') as fixture, \
             patch('examples.benchmark_matched_conditions.build') as build, \
             patch('examples.benchmark_matched_conditions.run_agent') as agent, \
             patch('sys.argv',['benchmark','--output',tmp+'/output','--launcher',tmp+'/missing']):
            with self.assertRaises(SystemExit) as error:main()
            self.assertEqual(2,error.exception.code)
            for mock in (fixture,build,agent):mock.assert_not_called()
            report=json.loads((Path(tmp)/'output/preflight.json').read_text())
            self.assertFalse(report['ready']);self.assertEqual(0,report['model_calls'])

    def test_selected_guide_hashes_references_separately_from_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);skill=root/'SKILL.md';skill.write_text('stage guide')
            reference=root/'references/safety.md';reference.parent.mkdir();reference.write_text('old')
            before=guide_evidence(skill)
            reference.write_text('updated coverage guidance')
            self.assertNotEqual(before['files'],guide_evidence(skill)['files'])
            self.assertIn('references/safety.md',before['files'])

    def test_missing_guide_stops_before_fixture_and_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.runtime(root/'app')
            with patch('examples.benchmark_matched_conditions.Fixture') as fixture, \
                 patch('examples.benchmark_matched_conditions.run_agent') as agent, \
                 patch('sys.argv',['benchmark','--output',str(root/'output'),
                     '--launcher',str(entry),'--tacto-guide',str(root/'missing/SKILL.md')]):
                with self.assertRaises(SystemExit) as error:main()
                self.assertEqual(2,error.exception.code)
                fixture.assert_not_called();agent.assert_not_called()

    def test_required_command_mismatch_is_reported_without_helper_or_model(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.runtime(root)
            spec=root/'skill/runtime-requirements.json';spec.write_text('{"schema":1,"command_help":["session"]}')
            runtime=runtime_evidence(entry);guide=guide_evidence(root/'skill/SKILL.md')
            self.assertIn('runtime-requirements.json',guide['files'])
            with patch('examples.benchmark_runtime.subprocess.run',return_value=CompletedProcess([],0,'{"supported":[]}','')) as probe:
                result=guide_runtime_compatibility(runtime,guide)
            self.assertFalse(result['compatible']);self.assertEqual(['session'],result['missing_commands'])
            self.assertEqual(str((root/'.venv/bin/python').resolve()),probe.call_args.args[0][0])
            self.assertNotIn('--helper',probe.call_args.args[0])

    def test_nonobject_requirements_are_rejected_before_a_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.runtime(root)
            (root/'skill/runtime-requirements.json').write_text('null')
            with patch('examples.benchmark_runtime.subprocess.run') as probe:
                with self.assertRaisesRegex(ValueError,'object'):
                    guide_runtime_compatibility(runtime_evidence(entry),guide_evidence(root/'skill/SKILL.md'))
                probe.assert_not_called()

    def test_prompt_pins_the_selected_runtime_skill_and_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.runtime(root)
            text=prompt({'amount':'1','factor':'2','bonus':False,'result':'2'},'owned.app','tacto',launcher=entry)
            self.assertIn(str(entry),text);self.assertIn(str(root/'skill/SKILL.md'),text)
            self.assertNotIn('~/.local/bin/tacto',text)
            override=root/'new-guide/SKILL.md'
            text=prompt({'amount':'1','factor':'2','bonus':False,'result':'2'},'owned.app','tacto',launcher=entry,tacto_guide=override)
            self.assertIn(str(override),text)
            self.assertNotIn(str(root/'skill/SKILL.md'),text)
