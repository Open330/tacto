import json
import socket
import threading
import unittest
from types import SimpleNamespace
from tacto.foreground_scope import foreground_scope
from tacto.window_scope import take_window_arg,bind_window
from tacto.helper import InheritedHelperDriver

class ParallelCoordination(unittest.TestCase):
    def test_window_id_is_live_host_scope_and_invalid_values_are_rejected(self):
        self.assertEqual((['app','--helper'],42),take_window_arg(['app','--window-id','42','--helper']))
        for value in ('0','-1','4294967296','1.5','True','１'):
            with self.subTest(value=value),self.assertRaises(ValueError):take_window_arg(['app','--window-id',value])
        with self.assertRaises(ValueError):take_window_arg(['--document','/owned/file','--window-id','2'])
        self.assertEqual('window_scope_unavailable',bind_window(object(),'app',42)['error'])

    def test_bound_channel_attaches_scope_and_never_sends_another_target(self):
        client,peer=socket.socketpair()
        driver=object.__new__(InheritedHelperDriver);driver._sock=client
        driver._f=client.makefile('rw',encoding='utf-8',newline='\n')
        driver.capabilities={'session.window_scope'};received=[]
        self.addCleanup(driver._f.close);self.addCleanup(client.close);self.addCleanup(peer.close)
        def server():
            stream=peer.makefile('rb')
            try:
                received.append(json.loads(stream.readline()))
                peer.sendall(b'{"type":"observation","tree":"owned"}\n')
            finally:stream.close()
        worker=threading.Thread(target=server);worker.start()
        self.assertIsNone(bind_window(driver,'app',42))
        self.assertEqual('observation',driver._send({'type':'observe','target':'app'})['type'])
        self.assertEqual('window_scope_changed',driver._send({'type':'observe','target':'other'})['error'])
        worker.join(2);self.assertFalse(worker.is_alive())
        self.assertEqual([{'type':'observe','target':'app','window_scope_id':42}],received)
        self.assertEqual('window_scope_changed',driver.scope_window('app',43)['error'])
        self.assertEqual('window_scope_requires_app',driver.foreground_begin('app')['error'])

    def test_busy_reply_does_not_cause_host_input_retransmission(self):
        client,peer=socket.socketpair();driver=object.__new__(InheritedHelperDriver)
        driver._sock=client;driver._f=client.makefile('rw',encoding='utf-8',newline='\n')
        self.addCleanup(driver._f.close);self.addCleanup(client.close);self.addCleanup(peer.close)
        received=[]
        def server():
            received.append(peer.recv(4096));peer.sendall(b'{"type":"error","error":"input_busy","effect":"not_started"}\n')
        worker=threading.Thread(target=server);worker.start()
        result=driver._send({'type':'act','target':'app','action':{'name':'click','element_index':2}})
        worker.join(2);self.assertEqual('input_busy',result['error']);self.assertEqual(1,len(received))

    def test_foreground_lease_wraps_success_failure_and_busy_without_repeating_body(self):
        calls=[]
        driver=SimpleNamespace(capabilities={'session.foreground_lease'},
            foreground_begin=lambda target:calls.append('begin') or {'type':'foreground_lease','acquired':True},
            foreground_end=lambda target:calls.append('end'))
        with foreground_scope(driver,'app') as error:
            self.assertIsNone(error);calls.append('activate')
        self.assertEqual(['begin','activate','end'],calls)
        calls.clear()
        with self.assertRaises(RuntimeError):
            with foreground_scope(driver,'app'):
                calls.append('activate');raise RuntimeError('owned failure')
        self.assertEqual(['begin','activate','end'],calls)
        calls.clear();driver.foreground_begin=lambda target:{'type':'error','error':'target_busy','effect':'not_started'}
        with foreground_scope(driver,'app') as error:self.assertEqual('target_busy',error['error'])
        self.assertEqual([],calls)
