"""The helper is opened by LaunchServices, and the launcher listens first.

Both halves of that are load-bearing and neither is visible at runtime until
something has already gone wrong. posix_spawn made the helper's screen-recording
grant unusable — TCC attributed the request to whatever spawned it — and the old
rendezvous, where the helper bound the socket and the launcher waited for it to
appear, left a window in which a same-user process could bind that path first
and answer in the helper's place. A regression to either would look like working
code.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = (ROOT / "launcher/tacto.swift").read_text()
HELPER = (ROOT / "helper/TactoHelper.swift").read_text()


class HowTheHelperIsStarted(unittest.TestCase):
    def test_the_launcher_opens_it_through_launchservices(self):
        self.assertIn("NSWorkspace.shared.openApplication(at: helperApp", LAUNCHER)

    def test_the_launcher_never_spawns_the_helper(self):
        # posix_spawn still starts the python child; what must not come back is
        # a spawned HELPER, whose grant would be attributed to this process.
        for match in re.finditer(r"posix_spawn\(&(\w+)", LAUNCHER):
            self.assertNotIn("helper", match.group(1).lower(),
                             "the helper must not be spawned: TCC would attribute "
                             "its screen-recording request to this launcher")

    def test_it_asks_for_a_new_instance_and_refuses_a_substitute(self):
        self.assertIn("configuration.createsNewApplicationInstance = true", LAUNCHER)
        self.assertIn("configuration.allowsRunningApplicationSubstitution = false",
                      LAUNCHER)

    def test_opening_the_helper_does_not_take_the_user_s_focus(self):
        self.assertIn("configuration.activates = false", LAUNCHER)


class WhichEndListens(unittest.TestCase):
    def test_the_launcher_binds_before_it_launches(self):
        bind_at = LAUNCHER.index("listen(listener, 1)")
        launch_at = LAUNCHER.index("NSWorkspace.shared.openApplication")
        self.assertLess(bind_at, launch_at,
                        "binding after the launch leaves the path unclaimed")

    def test_startHelper_launches_only_after_the_rendezvous_is_open(self):
        body = LAUNCHER[LAUNCHER.index("func startHelper()"):]
        body = body[:body.index("\n}\n")]
        self.assertLess(body.index("openRendezvous()"), body.index("launchHelper()"))

    def test_the_helper_connects_and_does_not_listen(self):
        self.assertIn('args.firstIndex(of: "--connect")', HELPER)
        self.assertNotIn('args.firstIndex(of: "--listen")', HELPER)
        self.assertNotIn("func serveSocket", HELPER)

    def test_the_helper_refuses_to_run_without_a_rendezvous(self):
        self.assertIn("guard connectPath != nil || persistentMode != nil else {", HELPER)
        self.assertIn('CommandLine.arguments.contains("--warm-server")', HELPER)
        self.assertIn('CommandLine.arguments.contains("--resident-agent")', HELPER)

    def test_the_helper_looks_the_flag_up_by_name(self):
        # LaunchServices may append arguments of its own, so a fixed position
        # would be read off the wrong argv.
        self.assertNotIn("CommandLine.arguments[1]", HELPER)


class WhoIsAccepted(unittest.TestCase):
    def test_the_peer_must_satisfy_the_code_requirement(self):
        body = LAUNCHER[LAUNCHER.index("func rendezvousAccepted"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("peerSatisfies(fd, helperRequirement)", body)

    def test_the_peer_must_be_the_instance_we_launched(self):
        body = LAUNCHER[LAUNCHER.index("func rendezvousAccepted"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("app.processIdentifier", body)

    def test_neither_side_requires_a_matching_audit_session(self):
        # An ssh-side launcher and the console-session helper it opens have
        # different audit sessions by design; measured 100228 against 100018.
        for name, source in (("launcher", LAUNCHER), ("helper", HELPER)):
            with self.subTest(side=name):
                for line in source.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("//") or ".val.6" not in stripped:
                        continue
                    # Reading the session id is fine and useful — a diagnostic
                    # that prints which session a process is in is how the
                    # difference was found. COMPARING two of them is the thing
                    # that could never hold.
                    if "==" in stripped or "!=" in stripped:
                        self.fail(f"{name} still compares audit sessions: {stripped}")

    def test_a_refusal_says_what_failed(self):
        self.assertIn("OSStatus", LAUNCHER)


class WhatEndsTheHelper(unittest.TestCase):
    def test_no_parent_pid_watchdog_remains(self):
        # getppid() == 1 means both "orphaned" and "launchd started me on
        # purpose", so a watchdog on it killed every LaunchServices launch.
        self.assertNotIn("if getppid() != parentPID", HELPER)

    def test_the_connection_owns_the_lifetime(self):
        body = HELPER[HELPER.index("func serveLauncher"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("serve(connection: conn)", body)
        self.assertIn("exit(0)", body[body.index("serve(connection: conn)"):])

    def test_the_launcher_terminates_the_instance_not_a_remembered_pid(self):
        tail = LAUNCHER[LAUNCHER.index("waitpid(childPID"):]
        self.assertIn("helperRunning", tail)
        self.assertIn("forceTerminate()", tail)
        self.assertNotIn("kill(helperPID", tail)

    def test_the_helper_gives_up_if_nobody_claims_it(self):
        self.assertIn("rendezvousDeadline", HELPER)


class WhereDiagnosticsGo(unittest.TestCase):
    def test_the_helper_logs_where_it_can_still_be_heard(self):
        # LaunchServices gives a launched app no stderr, so a refusal written
        # only there is a refusal nobody reads.
        self.assertIn('Logger(subsystem: "ai.callabo.tacto"', HELPER)

    def test_the_rendezvous_refusals_use_it(self):
        body = HELPER[HELPER.index("func connectToLauncher"):]
        body = body[:body.index("\n@Sendable func serveLauncher")]
        self.assertGreaterEqual(body.count("note("), 2)

    def test_the_launcher_points_at_the_log_when_the_helper_vanishes(self):
        self.assertIn("subsystem == ", LAUNCHER)


class NoTransportPretendsItCanConnect(unittest.TestCase):
    def test_the_python_socket_transport_is_gone(self):
        import tacto.helper as helper
        self.assertFalse(hasattr(helper, "SocketHelperDriver"),
                         "it spawned the helper with --listen, a mode that no "
                         "longer exists, and no python process can satisfy the "
                         "helper's compiled-in requirement anyway")

    def test_nothing_still_starts_the_helper_with_listen(self):
        self.assertNotIn('"--listen"', (ROOT / "tacto/helper.py").read_text())


if __name__ == "__main__":
    unittest.main()
