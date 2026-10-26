"""Opt-in real Safari + signed launcher + Keychain receiving test.

TACTO_RUN_NATIVE_CREDENTIAL_TESTS=1 python -m unittest discover -s tests -p test_credential_native_login.py -v
Uses only unique synthetic Keychain entries, removes them and its grants, and
never reads, unlocks or locks an external Vault. Requires a built source helper
bundle/launcher and existing macOS Accessibility permission.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from test_credential_browser import Page, SECRET
from tacto.credential_grants import CredentialStore
from tacto.credential_vault import KeychainVault

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(sys.platform=='darwin' and os.environ.get('TACTO_RUN_NATIVE_CREDENTIAL_TESTS')=='1','explicit native GUI credential fixture required')
class NativeLogin(unittest.TestCase):
    def test_real_safari_keychain_signed_input_receiving_and_reuse(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Page);server.received=[]
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def stop():server.shutdown();server.server_close();thread.join()
        self.addCleanup(stop)
        url=f'http://127.0.0.1:{server.server_port}'
        name='owned-native-'+uuid.uuid4().hex
        store=CredentialStore();vault=KeychainVault()
        store.add_vault(name,{'kind':'keychain'})
        self.addCleanup(lambda:store._path('vault',name).unlink(missing_ok=True))
        self.addCleanup(store.revoke,name)
        fields=[]
        for field,value in [('username','owned-user'),('password',SECRET)]:
            item=name+'-'+field;vault.put(item,value);self.addCleanup(vault.delete,item)
            fields.append({'name':field,'selector':{'role':'TextField','title':field.capitalize()},'secret':{'vault':name,'item':item,'field':'password'}})
        store.grant(name,{'target':'com.apple.Safari','origin':url,'level':'login','fields':fields,
            'submit':{'role':'Button','title':'Sign in'},'success':{'field':{'role':'Heading','title':'Signed in'},'attribute':'Title','equals':'Signed in'}})
        def command(*args):
            r=subprocess.run([str(ROOT/'launcher/tacto'),*args],cwd=ROOT,capture_output=True,text=True,timeout=40)
            self.assertNotIn(SECRET,r.stdout+r.stderr)
            result=json.loads(r.stdout);self.assertEqual(0,r.returncode,result)
            return result
        command('open-url',url,'--browser','com.apple.Safari','--helper')
        result=command('auth','login',name,'--helper')
        self.assertEqual('login_verified',result.get('status'),result)
        self.assertEqual([{'username':['owned-user'],'password':[SECRET]}],server.received)
        again=command('auth','login',name,'--helper')
        self.assertEqual('already_authenticated',again.get('status'),again)
        self.assertEqual(0,again['filled_fields']);self.assertEqual(1,len(server.received))
