"""Helper telemetry validation and passive PID-reuse/exit distinctions."""
from dataclasses import FrozenInstanceError
import json
import os
import socket
import subprocess
import unittest
from unittest.mock import patch
from tacto.helper import InheritedHelperDriver, REQUIRED_CAPABILITIES, HelperUnavailable
from tacto.helper_identity import HelperIdentityError, HelperProcessIdentity, parse_helper_identity, helper_instance_status
from tacto.native_process_identity import ProcessIdentityError


class HelperIdentityTests(unittest.TestCase):
    def test_optional_metadata_and_matching_os_peer(self):
        self.assertIsNone(parse_helper_identity(None))
        self.assertIsNone(parse_helper_identity(None, {'pid':9,'pid_version':2}))
        identity=parse_helper_identity({'pid':9,'start_time':123}, {'pid':9,'pid_version':2})
        self.assertEqual(identity, HelperProcessIdentity(9,123.0,2))
        with self.assertRaises(FrozenInstanceError): identity.pid=10

    def test_malformed_fields_and_peer_mismatch_do_not_leak_values(self):
        cases=[{'pid':True,'start_time':1},{'pid':0,'start_time':1}, {'pid':2**31,'start_time':1},
               {'pid':9,'start_time':True},{'pid':9,'start_time':float('nan')},
               {'pid':9,'start_time':float('inf')},{'pid':9,'start_time':-1},
               {'pid':9,'start_time':10**1000},{'pid':9,'start_time':'PRIVATE_SENTINEL'},
               {'pid':9,'start_time':1,'extra':'PRIVATE_SENTINEL'}]
        for value in cases:
            with self.assertRaises(HelperIdentityError) as error: parse_helper_identity(value)
            self.assertNotIn('PRIVATE_SENTINEL',str(error.exception))
        for peer in ({'pid':10,'pid_version':1},{'pid':9,'pid_version':True},
                     {'pid':9,'pid_version':2**32},{'pid':9},[]):
            with self.assertRaises(HelperIdentityError): parse_helper_identity({'pid':9,'start_time':1},peer)

    def test_passive_running_zombie_replaced_and_unobservable_states(self):
        identity=HelperProcessIdentity(9,123.0)
        for snapshot,expected in (({'start_time':123.0,'zombie':False},'same_instance'),
                                  ({'start_time':123.0,'zombie':True},'zombie'),
                                  ({'start_time':124.0,'zombie':False},'replaced')):
            with patch('tacto.helper_identity.process_snapshot',return_value=snapshot), patch('tacto.helper_identity.os.kill') as exists:
                self.assertEqual(helper_instance_status(identity),expected)
                exists.assert_not_called()
        for effect,expected in ((None,'unobservable'),(PermissionError(),'unobservable'),
                                (ProcessLookupError(),'exited')):
            with patch('tacto.helper_identity.process_snapshot',side_effect=ProcessIdentityError('unavailable')), \
                    patch('tacto.helper_identity.os.kill',side_effect=effect) as exists, \
                    patch('tacto.helper_identity.subprocess.run',return_value=type('Reply',(),{'stdout':'9 S','returncode':0})()):
                self.assertEqual(helper_instance_status(identity),expected)
                exists.assert_called_once_with(9,0)
        with patch('tacto.helper_identity.process_snapshot') as read, patch('tacto.helper_identity.os.kill') as exists:
            self.assertEqual(helper_instance_status(HelperProcessIdentity(9,float('nan'))),'unobservable')
            read.assert_not_called(); exists.assert_not_called()

    def test_zombie_fallback_requires_exact_pid_and_successful_bounded_state_read(self):
        identity=HelperProcessIdentity(9,123.0)
        for output,code,expected in (('9 Z',0,'zombie'),('9 Zs',0,'zombie'),('10 Z',0,'unobservable'),
                                     ('9 S',0,'unobservable'),('',1,'unobservable'),('9 Z',1,'unobservable')):
            with patch('tacto.helper_identity.process_snapshot',side_effect=ProcessIdentityError('unavailable')), \
                    patch('tacto.helper_identity.os.kill'), \
                    patch('tacto.helper_identity.subprocess.run',return_value=type('Reply',(),{'stdout':output,'returncode':code})()) as ps:
                self.assertEqual(helper_instance_status(identity),expected)
                self.assertEqual(ps.call_args.args[0],['/bin/ps','-p','9','-o','pid=,stat='])
                self.assertEqual(ps.call_args.kwargs['timeout'],.5)

    def test_failed_or_oversized_fallback_never_claims_exit(self):
        identity=HelperProcessIdentity(9,123.0)
        for effect in (OSError(), subprocess.TimeoutExpired('ps',.5)):
            with patch('tacto.helper_identity.process_snapshot',side_effect=ProcessIdentityError('unavailable')), \
                    patch('tacto.helper_identity.os.kill'), \
                    patch('tacto.helper_identity.subprocess.run',side_effect=effect):
                self.assertEqual(helper_instance_status(identity),'unobservable')
        with patch('tacto.helper_identity.process_snapshot',side_effect=ProcessIdentityError('unavailable')), \
                patch('tacto.helper_identity.os.kill'), \
                patch('tacto.helper_identity.subprocess.run',return_value=type('Reply',(),{'stdout':'9 Z'+' '*129,'returncode':0})()):
            self.assertEqual(helper_instance_status(identity),'unobservable')

    def test_real_inherited_handshake_keeps_legacy_and_rejects_malformed_identity(self):
        for metadata,valid in (({},True),({'helper_process':{'pid':9,'start_time':1},
            'helper_peer':{'pid':9,'pid_version':2}},True),
            ({'helper_process':{'pid':True,'start_time':1}},False),
            ({'helper_process':{'pid':9,'start_time':1},'helper_peer':{'pid':10,'pid_version':2}},False)):
            with self.subTest(metadata=metadata):
                client,peer=socket.socketpair(); descriptor=client.detach()
                try:
                    pong={'type':'pong','trusted':True,'capabilities':sorted(REQUIRED_CAPABILITIES),**metadata}
                    peer.sendall((json.dumps(pong)+'\n').encode())
                    with patch.object(InheritedHelperDriver,'_shared',None), patch.dict(os.environ,{'TACTO_HELPER_FD':str(descriptor)}):
                        if valid:
                            driver=InheritedHelperDriver()
                            self.assertEqual(driver.helper_process_identity is None,not bool(metadata))
                            driver._f.close(); driver._sock.close()
                        else:
                            with self.assertRaises(HelperUnavailable): InheritedHelperDriver()
                            with self.assertRaises(OSError): os.fstat(descriptor)
                finally:
                    peer.close()
                    try: os.close(descriptor)
                    except OSError: pass
