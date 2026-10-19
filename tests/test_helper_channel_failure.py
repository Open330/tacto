"""A broken response cannot authorize another request on the same channel."""
import json
import socket
import threading
import unittest
from unittest.mock import Mock

from tacto.helper import HelperUnavailable, InheritedHelperDriver


class HelperChannelFailureTests(unittest.TestCase):
    def exercise(self, reply, *, withhold=False):
        client, peer = socket.socketpair()
        driver = object.__new__(InheritedHelperDriver)
        driver._sock = client
        driver._f = client.makefile('rw', encoding='utf-8', newline='\n')
        self.addCleanup(driver._f.close)
        self.addCleanup(client.close)
        self.addCleanup(peer.close)
        received = []
        def server():
            stream = peer.makefile('rb')
            try:
                received.append(json.loads(stream.readline()))
                if withhold:
                    pass
                elif reply is not None:
                    peer.sendall(reply + b'\n{"type":"acted"}\n')
                else:
                    peer.shutdown(socket.SHUT_WR)
                following = stream.readline()
                if following:
                    received.append(json.loads(following))
            finally:
                stream.close()
        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        client.settimeout(.05 if withhold else 2)
        with self.assertRaises(HelperUnavailable) as failure:
            driver._send({'type':'act','action':{'name':'menu','path':['File','Save']}})
        self.assertNotIn('PRIVATE_DOCUMENT_SENTINEL', str(failure.exception))
        with self.assertRaises(HelperUnavailable):
            driver._send({'type':'act','action':{'name':'menu','path':['File','Save']}})
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(received), 'a broken channel sent a second action')
        self.assertTrue(driver._poisoned)

    def test_malformed_reply_does_not_consume_queued_ack_on_retry(self):
        self.exercise(b'PRIVATE_DOCUMENT_SENTINEL not-json')

    def test_non_object_reply_does_not_consume_queued_ack_on_retry(self):
        self.exercise(b'[]')

    def test_eof_poisoned_even_when_peer_still_accepts_writes(self):
        self.exercise(None)

    def test_timeout_never_sends_another_action(self):
        self.exercise(None, withhold=True)

    def test_invalid_response_envelope_never_consumes_queued_ack(self):
        for reply in (b'{}', b'{"type":null}', b'{"type":""}', b'{"type":2}'):
            with self.subTest(reply=reply):
                self.exercise(reply)

    def test_valid_error_reply_keeps_channel_usable(self):
        client, peer = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(peer.close)
        driver = object.__new__(InheritedHelperDriver)
        driver._sock = client
        driver._f = client.makefile('rw', encoding='utf-8', newline='\n')
        self.addCleanup(driver._f.close)
        peer.sendall(b'{"type":"error","error":"target_missing"}\n{"type":"pong"}\n')
        with self.assertRaises(TypeError):
            driver._send({'invalid':object()})
        self.assertEqual('error', driver._send({'type':'act'})['type'])
        self.assertEqual('pong', driver._send({'type':'ping'})['type'])
        self.assertFalse(getattr(driver, '_poisoned', False))

    def test_interrupt_poisoned_before_another_request(self):
        client, peer = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(peer.close)
        driver = object.__new__(InheritedHelperDriver)
        driver._sock = client
        driver._f = Mock()
        driver._f.write.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            driver._send({'type':'act'})
        with self.assertRaises(HelperUnavailable):
            driver._send({'type':'act'})
        driver._f.close.assert_called_once()
        self.assertEqual(1, driver._f.write.call_count)
        peer.settimeout(1)
        self.assertEqual(b'', peer.recv(1))
