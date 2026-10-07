"""Commands release sleep assertions and failed helper launches own no pipes."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nlc import cli
from nlc.helper import HelperDriver, HelperUnavailable, InheritedHelperDriver
from nlc.session import KeepAwake


class CommandLifetimeTests(unittest.TestCase):
    def test_every_screen_command_releases_on_return_and_exception(self):
        for command in ("doctor", "apps", "observe", "run", "replay", "do", "eval"):
            for failure in (None, SystemExit(2), KeyboardInterrupt()):
                with self.subTest(command=command, failure=failure), \
                     patch("nlc.session.sandboxed", return_value=False), \
                     patch("nlc.session.KeepAwake") as awake, \
                     patch("nlc.cli._dispatch", return_value=7, side_effect=failure):
                    if failure is None:
                        self.assertEqual(cli.main([command]), 7)
                    else:
                        with self.assertRaises(type(failure)):
                            cli.main([command])
                    awake.return_value.__enter__.assert_called_once()
                    awake.return_value.__exit__.assert_called_once()

    def test_no_assertion_for_offscreen_or_sandboxed_command(self):
        with patch("nlc.session.KeepAwake") as awake, \
             patch("nlc.session.sandboxed", return_value=True), \
             patch("nlc.cli._dispatch", return_value=0), patch("builtins.print"):
            self.assertEqual(cli.main(["list"]), 0)
            self.assertEqual(cli.main(["observe"]), 3)
            awake.assert_not_called()

    def test_caffeinate_is_reaped_at_context_exit(self):
        with KeepAwake() as hold:
            proc = hold._proc
            self.assertIsNotNone(proc)
            self.assertIsNone(proc.poll())
        self.assertIsNotNone(proc.returncode)
        self.assertIsNone(hold._proc)


class HelperLifetimeTests(unittest.TestCase):
    def launch(self, body, error=HelperUnavailable):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "helper"
        path.write_text(f"#!{sys.executable}\nimport sys, json\n" + body)
        path.chmod(0o700)
        spawned = []
        popen = subprocess.Popen

        def record(*args, **kwargs):
            proc = popen(*args, **kwargs)
            spawned.append(proc)
            self.addCleanup(self.cleanup_process, proc)
            return proc

        with patch("nlc.helper.subprocess.Popen", side_effect=record):
            with self.assertRaises(error):
                HelperDriver(str(path))
        self.assertEqual(len(spawned), 1)
        proc = spawned[0]
        self.assertIsNotNone(proc.returncode)
        self.assertTrue(all(s.closed for s in (proc.stdin, proc.stdout, proc.stderr)))

    @staticmethod
    def cleanup_process(proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()

    def test_peer_rejection(self):
        self.launch("sys.exit(3)\n")

    def test_untrusted_helper(self):
        self.launch('sys.stdin.readline()\nprint(json.dumps({"type": "pong", "trusted": False}), flush=True)\nsys.stdin.read()\n')

    def test_unexpected_handshake(self):
        self.launch('sys.stdin.readline()\nprint(json.dumps({"type": "wrong"}), flush=True)\nsys.stdin.read()\n')

    def test_malformed_handshake(self):
        # JSON parsing errors also clean up the launched child.
        self.launch('sys.stdin.readline()\nprint("broken JSON", flush=True)\nsys.stdin.read()\n', ValueError)

    def test_closed_stdout_with_living_child_is_bounded(self):
        self.launch('import os, time\nos.close(1)\ntime.sleep(60)\n')


class InheritedChannelLifetimeTests(unittest.TestCase):
    def test_failed_handshake_releases_real_inherited_descriptor(self):
        for response, error in (({'type': 'pong', 'trusted': False}, HelperUnavailable),
                                ({'type': 'pong', 'trusted': True}, HelperUnavailable),
                                ([], HelperUnavailable),
                                ('malformed', ValueError)):
            with self.subTest(response=response):
                child, peer = socket.socketpair()
                descriptor = child.detach()
                try:
                    data = response if isinstance(response, str) else json.dumps(response)
                    peer.sendall((data + '\n').encode())
                    with patch.object(InheritedHelperDriver, '_shared', None), \
                         patch.dict(os.environ, {'NLC_HELPER_FD': str(descriptor)}):
                        with self.assertRaises(error):
                            InheritedHelperDriver()
                        self.assertIsNone(InheritedHelperDriver._shared)
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)
                    peer.settimeout(1)
                    while peer.recv(4096):
                        pass
                finally:
                    peer.close()
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    def test_caller_interrupt_also_releases_channel(self):
        child, peer = socket.socketpair()
        descriptor = child.detach()
        try:
            with patch.object(InheritedHelperDriver, '_shared', None), \
                 patch.dict(os.environ, {'NLC_HELPER_FD': str(descriptor)}), \
                 patch.object(InheritedHelperDriver, '_send', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    InheritedHelperDriver()
                self.assertIsNone(InheritedHelperDriver._shared)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            peer.settimeout(1)
            self.assertEqual(peer.recv(1), b'')
        finally:
            peer.close()
            try:
                os.close(descriptor)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
