"""A skipped suite has to be the screen's fault, not a guess about it.

2026-09-20: with Calculator and TextEdit closed and Finder holding no windows,
the whole native suite skipped with "화면이 잠겨 있거나 GUI 세션을 볼 수
없음" — on an unlocked screen with someone sitting at it. The check observed
three apps and called the screen dark if every RUNNING one came back empty,
which with one running app is one sample reading zero.

A suite that skips itself is worse than one that fails: a failure gets looked
at, and a skip reads as "nothing to see here".
"""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_eval():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location('eval_under_test',
                                                  ROOT / 'examples/eval.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(importlib.util.find_spec('Quartz'), 'Quartz required')
class GuiAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.m = load_eval()

    def reason(self, session, trees=()):
        """`trees` is what each running app's observation reports."""
        import Quartz
        remaining = list(trees)
        def resolve(app, launch=False):
            # Exactly as many apps are running as there are trees to report.
            return 1 if remaining else None
        def observe(app, mode, require_window=False):
            return {'element_count': remaining.pop(0)}
        with patch.object(Quartz, 'CGSessionCopyCurrentDictionary', return_value=session), \
             patch.object(self.m, 'Driver') as driver:
            instance = driver.return_value
            instance._resolve.side_effect = resolve
            instance.observe.side_effect = observe
            return self.m._gui_reason()

    ON_CONSOLE = {'kCGSSessionOnConsoleKey': True}

    def test_an_unlocked_console_session_is_usable_whatever_the_apps_show(self):
        """The window server knows. Nothing an app does changes the answer."""
        self.assertEqual('', self.reason(self.ON_CONSOLE, trees=(0,)))
        self.assertEqual('', self.reason(self.ON_CONSOLE, trees=(0, 0, 0)))
        self.assertEqual('', self.reason(self.ON_CONSOLE, trees=()))

    def test_a_locked_screen_says_it_is_locked(self):
        reason = self.reason({**self.ON_CONSOLE, 'CGSSessionScreenIsLocked': 1})
        self.assertIn('잠', reason)

    def test_being_off_the_console_is_named_as_that_and_not_as_a_lock(self):
        reason = self.reason({'kCGSSessionOnConsoleKey': False})
        self.assertNotEqual('', reason)
        self.assertIn('세션', reason)

    def test_without_a_session_dictionary_one_empty_app_proves_nothing(self):
        """The case that actually happened: Finder alone, holding no windows."""
        self.assertEqual('', self.reason(None, trees=(0,)))

    def test_without_a_session_dictionary_every_app_empty_still_speaks(self):
        self.assertNotEqual('', self.reason(None, trees=(0, 0)))

    def test_without_a_session_dictionary_any_content_means_usable(self):
        self.assertEqual('', self.reason(None, trees=(0, 12)))


class ALockThatArrivesMidRun(unittest.TestCase):
    """The screen is checked once, before anything starts.

    2026-09-22: it locked partway through a run, and eight screen-touching
    scenarios turned into eight failures. Read as eight defects, they were one
    sentence — and the same shape as every other mistake in this suite's
    history, where a failure got explained instead of diagnosed.

    A lock observed after a failure is context, not proof of its cause. Keep
    the failed result and report the lock alongside it.
    """

    def _run(self, scenario_ok, gui_reasons):
        """Run `main()` over one gui scenario and return what it printed."""
        import io
        import contextlib
        spec = importlib.util.spec_from_file_location(
            "eval_under_test", ROOT / "examples/eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.SCENARIOS = [("probe_scenario (one gui check)", lambda: scenario_ok)]
        module.ISOLATED = set()
        module.SCENARIO_REQUIREMENTS = {"probe_scenario": {"gui"}}
        # The suite opens the apps it drives; a test of the lock logic should
        # not wait on LaunchServices to do it.
        module._ensure_window = lambda *a, **k: True
        reasons = list(gui_reasons)
        module._gui_reason = lambda: reasons.pop(0) if reasons else ""
        out = io.StringIO()
        argv = sys.argv
        sys.argv = ["eval.py"]
        try:
            with contextlib.redirect_stdout(out):
                try:
                    module.main()
                except SystemExit:
                    pass
        finally:
            sys.argv = argv
        return out.getvalue()

    def test_a_failure_after_a_lock_keeps_the_failure_and_reports_the_lock(self):
        # Unlocked at the start, locked by the time it failed.
        printed = self._run(False, ["", "화면이 잠겨 있음"])
        self.assertNotIn("SKIP", printed)
        self.assertIn("화면이 잠겨 있음", printed)
        self.assertIn("FAIL", printed)
        self.assertIn("0/1", printed)

    def test_a_real_failure_on_a_live_screen_still_fails(self):
        # The screen was fine throughout. Nothing here may absorb that.
        printed = self._run(False, ["", ""])
        self.assertIn("FAIL", printed)
        self.assertNotIn("SKIP", printed)
        self.assertIn("0/1", printed)

    def test_a_pass_is_never_second_guessed(self):
        printed = self._run(True, ["", "화면이 잠겨 있음"])
        self.assertIn("PASS", printed)
        self.assertIn("1/1", printed)


class TakingTheScreenIsOptIn(unittest.TestCase):
    """Nine scenarios pulled apps in front of whoever was using the machine.

    Measured on an owned fixture, in the background (2026-09-22):

        Direct Command    (target: the app delegate)    enabled = True
        Responder Command (target: the responder chain) enabled = False

    A menu item dispatched through the responder chain is validated against the
    KEY window, and a background app has no key window — `File > Save As…` is
    that kind, `File > Open…` is not and answers enabled from the background.
    A background window cannot be made key either: `makeKey()` was called, its
    counter moved, `isKeyWindow` stayed false.

    So the driver is right to refuse with `needs_foreground`. What was wrong
    was the suite: it wanted the success path, so it took the screen. This is
    not a guard around that — the capability is scoped out of the default run,
    which is the difference between "we check first" and "the code is not
    reachable".
    """

    def setUp(self):
        # The switch is process-wide on purpose — every harness that could take
        # focus reads the same one. That also means a test which turns it on
        # leaves it on for the next, so it is restored here rather than left
        # for the next test to discover.
        from nlc import evaluation
        was = evaluation.foreground_allowed()
        self.addCleanup(evaluation.allow_foreground, was)
        evaluation.allow_foreground(False)

    def _module(self):
        spec = importlib.util.spec_from_file_location(
            "eval_fg", ROOT / "examples/eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_bringing_an_app_forward_refuses_by_default(self):
        module = self._module()
        self.assertFalse(module.FOREGROUND_OK)
        # Refused inside the function, not at each call site: a guard every
        # caller has to remember is one a new caller will not.
        self.assertFalse(module._bring_forward("com.apple.TextEdit", None))

    def test_every_scenario_that_reaches_for_focus_declares_it(self):
        import re
        source = (ROOT / "examples/eval.py").read_text()
        blocks = re.split(r"\ndef (scenario_\w+)\(", source)
        module = self._module()
        for i in range(1, len(blocks), 2):
            name = blocks[i][len("scenario_"):]
            body = blocks[i + 1].split("\ndef ")[0]
            if "_textedit_ready" not in body and "_bring_forward" not in body:
                continue
            with self.subTest(scenario=name):
                self.assertIn("foreground",
                              module.SCENARIO_REQUIREMENTS.get(name, set()),
                              f"{name} takes the screen without declaring it")

    def test_a_foreground_scenario_is_skipped_in_a_default_run(self):
        import io, contextlib
        module = self._module()
        module.SCENARIOS = [("fg_probe (needs the key window)", lambda: True)]
        module.ISOLATED = set()
        module.SCENARIO_REQUIREMENTS = {"fg_probe": {"gui", "foreground"}}
        module._ensure_window = lambda *a, **k: True
        module._gui_reason = lambda: ""
        out = io.StringIO()
        argv = sys.argv
        sys.argv = ["eval.py"]
        try:
            with contextlib.redirect_stdout(out):
                try:
                    module.main()
                except SystemExit:
                    pass
        finally:
            sys.argv = argv
        printed = out.getvalue()
        self.assertIn("SKIP", printed)
        self.assertIn("--foreground", printed)
        self.assertNotIn("PASS", printed)

    def test_asking_for_it_says_so_and_runs_it(self):
        import io, contextlib
        module = self._module()
        module.SCENARIOS = [("fg_probe (needs the key window)", lambda: True)]
        module.ISOLATED = set()
        module.SCENARIO_REQUIREMENTS = {"fg_probe": {"gui", "foreground"}}
        module._ensure_window = lambda *a, **k: True
        module._gui_reason = lambda: ""
        out = io.StringIO()
        argv = sys.argv
        sys.argv = ["eval.py", "--foreground"]
        try:
            with contextlib.redirect_stdout(out):
                try:
                    module.main()
                except SystemExit:
                    pass
        finally:
            sys.argv = argv
        printed = out.getvalue()
        self.assertIn("PASS", printed)
        # And it warns, because the person may not be expecting to lose the
        # screen they are looking at.
        self.assertIn("빼앗습니다", printed)


class EverySwitchIsTheSameSwitch(unittest.TestCase):
    """A guard that covers only the paths you remembered is not a guard.

    `examples/eval.py` and `tests/test_helper_contract.py` each have their own
    `_bring_forward`. Gating the first left the second wide open, so
    `every_transport_answers_the_same` went on pulling Finder and the select
    fixture in front of whoever was working — from a run that believed it could
    not take focus at all.

    The earlier test only read scenario BODIES for `_bring_forward`, and that
    scenario reaches focus through another module, so it saw nothing. These
    check the property itself.
    """

    def setUp(self):
        from nlc import evaluation
        was = evaluation.foreground_allowed()
        self.addCleanup(evaluation.allow_foreground, was)
        evaluation.allow_foreground(False)

    def test_the_contract_harness_refuses_by_default_too(self):
        spec = importlib.util.spec_from_file_location(
            "contract_fg", ROOT / "tests/test_helper_contract.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertFalse(module._bring_forward("com.apple.finder", None))

    def test_every_activation_in_the_tree_is_behind_the_switch(self):
        """Found by reading the source, because the last one was missed.

        Anything that calls `activateWithOptions_` is taking the screen. Each
        such file has to consult the shared switch — not its own copy of a
        flag, which is how the two drifted apart.
        """
        import re
        roots = [ROOT / "examples", ROOT / "tests", ROOT / "nlc", ROOT / "helper"]
        offenders = []
        for root in roots:
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8", errors="replace")
                # A CALL, not a mention. `test_native_input_safety.py` keeps
                # the same name in a list of symbols that must not appear in
                # shipped code, and that file is the opposite of an offender.
                takes = [line for line in text.splitlines()
                         if ".activateWithOptions_(" in line
                         and not line.strip().startswith("#")]
                if not takes:
                    continue
                if "foreground_allowed" not in text:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders,
                         "these take the screen without consulting the shared "
                         "switch: " + ", ".join(offenders))

    def test_a_fixture_that_activates_itself_is_behind_it_too(self):
        """Focus has more than one door.

        The select fixture calls `activate(ignoringOtherApps:)` when launched
        with `--foreground`, which has nothing to do with `_bring_forward`.
        Gating one and not the other left a run that believed it could not take
        the screen pulling the fixture in front of it — measured: frontmost
        went Finder -> NLC Select Fixture.
        """
        text = (ROOT / "tests/test_helper_contract.py").read_text()
        launches = [line for line in text.splitlines()
                    if '"--foreground"' in line and not line.strip().startswith("#")]
        self.assertTrue(launches, "the fixture launch line moved")
        for line in launches:
            self.assertIn("foreground_allowed()", line,
                          f"unconditional --foreground: {line.strip()}")


class TheMiddleOfARunIsWitnessed(unittest.TestCase):
    """Two ends that match say nothing about the middle.

    A peer review pointed out that "frontmost was Finder before and after"
    cannot see an app pulled forward and handed back in between. Measured with
    a positive control: Calculator activated for 0.4s and Finder restored —
    both ends read Finder, the witness recorded ['calculator', 'finder'].
    """

    def test_the_focus_sensitive_probes_fail_on_any_activation(self):
        for name in ("background-menu-refusal.py", "canvas-point-click.py"):
            with self.subTest(probe=name):
                text = (ROOT / "examples/probes" / name).read_text()
                self.assertIn("_witness_focus()", text)
                self.assertIn("if report['activations']:", text)

    def test_the_witness_subscribes_rather_than_polls(self):
        # A poll every N ms cannot see a change shorter than N ms, and "we
        # sampled and saw nothing" is a weaker claim than "nothing happened".
        text = (ROOT / "examples/probes/focus_witness.py").read_text()
        self.assertIn("NSWorkspaceDidActivateApplicationNotification", text)

    def test_the_witness_is_armed_before_it_is_trusted(self):
        # An activation before the subscription existed would be missed.
        text = (ROOT / "examples/probes/focus_witness.py").read_text()
        self.assertIn('"event": "ready"', text)
        self.assertIn('raise RuntimeError("focus witness did not become ready")', text)


class ALaunchIsAnActivationUnlessToldOtherwise(unittest.TestCase):
    """`open -a` activates the app it opens.

    Dropping `--foreground` from the select fixture's launch did not stop it
    coming forward, because LaunchServices activates by default. The focus
    witness caught it in a default run — select-fixture, then finder — while
    the frontmost app read Finder at both ends.
    """

    def test_every_harness_launch_asks_for_the_background(self):
        import re
        offenders = []
        for root in (ROOT / "examples", ROOT / "tests"):
            for path in root.rglob("*.py"):
                for number, line in enumerate(
                        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if re.search(r'\["open",\s*"-(n|a|b)"', stripped) and '"-g"' not in stripped:
                        offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual([], offenders,
                         "launches that will activate the app: " + ", ".join(offenders))
