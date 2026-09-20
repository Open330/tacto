"""The identity each side claims must be the identity the other side demands.

Four copies of one sentence: the designated requirement the build scripts stamp
into each signature, and the acceptance predicate each Swift source applies to
its peer. Drift between them is not a compile error and not a test failure
anywhere else — it is a connection that is refused at runtime, on a machine that
already has the grant, with nothing in the message saying why. So they are
compared literally here.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEAM = "48H4RZNB97"
LAUNCHER_ID = "ai.callabo.nlc"
HELPER_ID = "ai.callabo.nlc.helper"


def requirement(identifier):
    return (f'identifier "{identifier}" and anchor apple generic and '
            f'certificate leaf[subject.OU] = "{TEAM}"')


def shell_requirement(identifier):
    """What signing.sh produces, asked of signing.sh itself."""
    out = subprocess.run(
        ["bash", "-c", f'. "{ROOT}/signing.sh"; nlc_requirement "{identifier}"'],
        capture_output=True, text=True, check=True)
    return out.stdout


def swift_literal(source, name):
    """The value of a `let <name> = "..." + "..."` string concatenation."""
    text = (ROOT / source).read_text()
    m = re.search(rf'^let {name} =\n((?:\s+"[^\n]*"\s*\+?\n)+)', text, re.M)
    assert m, f"{name} not found in {source}"
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))).replace('\\"', '"')


class ShellDefinition(unittest.TestCase):
    def test_signing_sh_agrees_with_the_expected_text(self):
        self.assertEqual(shell_requirement(LAUNCHER_ID), requirement(LAUNCHER_ID))
        self.assertEqual(shell_requirement(HELPER_ID), requirement(HELPER_ID))

    def test_every_build_script_sources_the_shared_definition(self):
        for script in ("launcher/build.sh", "helper/build.sh", "helper/bundle.sh"):
            with self.subTest(script=script):
                self.assertIn(". ../signing.sh", (ROOT / script).read_text())

    def test_no_build_script_pins_a_certificate_common_name(self):
        # The requirement codesign writes unprompted names one developer's
        # certificate. Naming it here would put that expiry date back in.
        for script in ("launcher/build.sh", "helper/build.sh", "helper/bundle.sh",
                       "signing.sh"):
            with self.subTest(script=script):
                self.assertNotIn("subject.CN", (ROOT / script).read_text())


class SwiftAcceptancePredicates(unittest.TestCase):
    def test_helper_requires_the_launcher_identity(self):
        self.assertEqual(swift_literal("helper/NLCHelper.swift", "clientRequirement"),
                         requirement(LAUNCHER_ID))

    def test_launcher_requires_the_helper_identity(self):
        self.assertEqual(swift_literal("launcher/nlc.swift", "helperRequirement"),
                         requirement(HELPER_ID))

    def test_neither_predicate_is_read_from_the_environment(self):
        for source in ("helper/NLCHelper.swift", "launcher/nlc.swift"):
            with self.subTest(source=source):
                text = (ROOT / source).read_text()
                for line in text.splitlines():
                    if "Requirement" in line and "=" in line and "//" not in line:
                        self.assertNotIn("ProcessInfo", line)
                        self.assertNotIn("getenv", line)


class SignedArtifacts(unittest.TestCase):
    """Skipped when nothing has been built; never passed by assuming."""

    def designated(self, path):
        target = ROOT / path
        if not target.exists():
            self.skipTest(f"{path} 이 빌드되어 있지 않습니다 — ./build.sh 를 먼저 실행하세요")
        out = subprocess.run(["codesign", "-d", "-r-", str(target)],
                             capture_output=True, text=True)
        if "not signed" in out.stderr or out.returncode != 0:
            self.skipTest(f"{path} 이 서명되어 있지 않습니다")
        m = re.search(r"^designated => (.+)$", out.stdout, re.M)
        self.assertIsNotNone(m, f"{path} 에 designated requirement 가 없습니다")
        return m.group(1).strip()

    def test_launcher_is_stamped_with_what_the_helper_demands(self):
        self.assertEqual(self.designated("launcher/nlc"), requirement(LAUNCHER_ID))

    def test_bundle_is_stamped_with_what_the_launcher_demands(self):
        self.assertEqual(self.designated("helper/NLC Helper.app"),
                         requirement(HELPER_ID))

    def test_the_bare_helper_carries_the_same_identity_as_the_bundle(self):
        # It was signed as "nlc-helper" once, an identifier the launcher's
        # predicate names nowhere, so the fallback could never authenticate.
        self.assertEqual(self.designated("helper/nlc-helper"), requirement(HELPER_ID))


if __name__ == "__main__":
    unittest.main()
