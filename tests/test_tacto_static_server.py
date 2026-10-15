"""The LAN preview serves public site assets, never the checkout or symlinks."""
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tacto_static', ROOT/'sites/tacto/serve.py')
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


class StaticServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.dist = self.root/'dist'; self.dist.mkdir()
        (self.dist/'index.html').write_text('<h1>Tacto</h1>')
        (self.dist/'assets').mkdir()
        (self.dist/'assets/app.js').write_text('console.log("Tacto")')
        (self.root/'private.txt').write_text('private outside site')
        old = site.DIRECTORY; site.DIRECTORY = self.dist.resolve()
        self.addCleanup(setattr, site, 'DIRECTORY', old)
        class Quiet(site.StaticHandler):
            def log_message(self, *args):
                pass
        self.server = site.ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_landing_and_assets_are_served_with_browser_protection(self):
        with urlopen(self.url) as response:
            self.assertEqual(b'<h1>Tacto</h1>', response.read())
            self.assertEqual('nosniff', response.headers['X-Content-Type-Options'])
            self.assertEqual('DENY', response.headers['X-Frame-Options'])
            self.assertIn("connect-src 'none'", response.headers['Content-Security-Policy'])
            self.assertEqual('no-referrer', response.headers['Referrer-Policy'])
        with urlopen(self.url+'/assets/app.js') as response:
            self.assertIn(b'Tacto', response.read())

    def test_source_and_parent_paths_cannot_be_downloaded(self):
        for path in ['/serve.py','/.git/config','/%2e%2e/private.txt','/../private.txt']:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.url+path)
            self.assertEqual(404, error.exception.code)

    def test_asset_directory_has_no_listing(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url+'/assets/')
        self.assertEqual(404,error.exception.code)

    def test_symlink_targets_inside_or_outside_site_are_not_served(self):
        for name,target in [('outside.txt',self.root/'private.txt'),('inside.html',self.dist/'index.html')]:
            (self.dist/name).symlink_to(target)
            with self.subTest(name=name), self.assertRaises(HTTPError) as error:
                urlopen(self.url+'/'+name)
            self.assertEqual(404,error.exception.code)

    def test_head_works_and_post_has_no_action(self):
        with urlopen(Request(self.url,method='HEAD')) as response:
            self.assertEqual(200,response.status)
            self.assertEqual(b'',response.read())
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url,data=b'no action',method='POST'))
        self.assertEqual(501,error.exception.code)
