import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from examples.probes.signed_popup_journal_restart import main,journal_contents
try:
    from tests._artifacts import requires_signed_build
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_signed_build

class SignedPopupJournalRestartTests(unittest.TestCase):
    @requires_signed_build
    def test_existing_report_or_invalid_pid_cannot_start_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();output=base/'report.json';output.write_text('KEEP');driver=Mock()
            with self.assertRaises(FileExistsError):main(driver,output,42)
            self.assertEqual('KEEP',output.read_text());driver.observe.assert_not_called()
            output.unlink();result=main(driver,output,True)
            self.assertFalse(result['passed']);self.assertEqual('owned_pid_required',result['error'])
            self.assertEqual(['report.json'],[p.name for p in base.iterdir()]);driver.observe.assert_not_called()

    def test_reserved_unknown_or_extra_slot_cannot_prove_success(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve()
            for n in range(4):
                (base/f'{n:05d}.jsonl').write_text(json.dumps({'state':'reserved'})+'\n'+json.dumps({'state':'reply_received','category':'acted'})+'\n')
            before=journal_contents(base);self.assertEqual(4,len(before))
            slot=base/'00003.jsonl';good=slot.read_text()
            for bad in (json.dumps({'state':'reserved'})+'\n',good.replace('acted','unknown'),good+'{}\n'):
                slot.write_text(bad)
                with self.assertRaises(ValueError):journal_contents(base)
            slot.write_text(good);self.assertEqual(before,journal_contents(base))
            (base/'00004.jsonl').write_text('')
            with self.assertRaises(ValueError):journal_contents(base)

    def test_worker_config_refuses_alias_public_parent_and_malformed_pid(self):
        from examples.probes.signed_popup_journal_restart import dispatch_worker
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();config=base/'config.json';config.write_text('{"expected_pid": true}');config.chmod(0o600)
            alias=base/'alias';alias.symlink_to(config)
            driver=Mock()
            for path in (config,alias):
                with self.assertRaises(ValueError):dispatch_worker(driver,path)
            config.chmod(0o644)
            with self.assertRaises(ValueError):dispatch_worker(driver,config)
            driver.observe.assert_not_called();driver.act.assert_not_called()

    def test_internal_worker_option_is_scoped_to_exact_worker(self):
        import contextlib,io
        from examples.eval import _parse_plan
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parse_plan(['signed_native_select_journal_restart','--select-journal-worker-config','/unused'])
        # Plan parsing does not read the config or execute the worker.
        _parse_plan(['signed_native_select_journal_dispatch_worker','--select-journal-worker-config','/unused','--preview'])
