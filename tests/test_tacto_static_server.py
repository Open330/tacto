"""The LAN preview serves public site assets, never the checkout or symlinks."""
import gzip
import os
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


    def test_gzip_matches_identity_and_zero_quality_is_respected(self):
        payload = (b"body{color:#123456}\n" * 300)
        (self.dist/'assets/large.css').write_bytes(payload)
        url = self.url+'/assets/large.css'
        with urlopen(Request(url,headers={'Accept-Encoding':'gzip'})) as response:
            packed=response.read()
            self.assertEqual('gzip',response.headers['Content-Encoding'])
            self.assertIn('Accept-Encoding',response.headers['Vary'])
            compressed_etag=response.headers['ETag']
            self.assertEqual(payload,gzip.decompress(packed))
            self.assertLess(len(packed),len(payload))
        for accepted in ('gzip;q=0','*;q=1, gzip;q=0','gzip;q=invalid'):
            with self.subTest(accepted=accepted), urlopen(Request(url,headers={'Accept-Encoding':accepted})) as response:
                self.assertIsNone(response.headers.get('Content-Encoding'))
                self.assertEqual(payload,response.read())
                self.assertNotEqual(compressed_etag,response.headers['ETag'])

    def test_matching_etag_returns_empty_304_for_exact_or_weak_tag(self):
        with urlopen(self.url) as response:
            etag=response.headers['ETag']
        for condition in (etag,'W/'+etag,'"unrelated", '+etag,'*'):
            with self.subTest(condition=condition), self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url,headers={'If-None-Match':condition}))
            self.assertEqual(304,error.exception.code)
            self.assertEqual(b'',error.exception.read())
            self.assertEqual(etag,error.exception.headers['ETag'])

    def test_same_size_edit_with_preserved_mtime_changes_etag(self):
        path=self.dist/'index.html'
        with urlopen(self.url) as response:
            old_etag=response.headers['ETag']
        old=path.stat()
        path.write_bytes(b'<h1>Fresh</h1>')
        os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
        with urlopen(Request(self.url,headers={'If-None-Match':old_etag})) as response:
            self.assertEqual(200,response.status)
            self.assertEqual(b'<h1>Fresh</h1>',response.read())
            self.assertNotEqual(old_etag,response.headers['ETag'])

    def test_conditional_gzip_head_has_same_representation_headers(self):
        (self.dist/'assets/large.css').write_bytes(b'a{color:red}'*500)
        url=self.url+'/assets/large.css'
        with urlopen(Request(url,headers={'Accept-Encoding':'gzip'})) as response:
            headers=response.headers
            response.read()
        with urlopen(Request(url,headers={'Accept-Encoding':'gzip'},method='HEAD')) as response:
            self.assertEqual(headers['ETag'],response.headers['ETag'])
            self.assertEqual(headers['Content-Length'],response.headers['Content-Length'])
            self.assertEqual('gzip',response.headers['Content-Encoding'])
            self.assertEqual(b'',response.read())


    def test_nonmatching_etag_takes_precedence_over_a_future_modified_since(self):
        headers={'If-None-Match':'"stale"','If-Modified-Since':'Wed, 01 Jan 2098 00:00:00 GMT'}
        with urlopen(Request(self.url,headers=headers)) as response:
            self.assertEqual(200,response.status)
            self.assertEqual(b'<h1>Tacto</h1>',response.read())

    def test_large_files_and_fifo_are_refused_without_reading(self):
        oversized=self.dist/'large.bin'
        with oversized.open('wb') as stream:
            stream.truncate(site.MAX_ASSET_BYTES+1)
        os.mkfifo(self.dist/'pipe')
        for name in ['large.bin','pipe']:
            with self.subTest(name=name),self.assertRaises(HTTPError) as error:
                urlopen(self.url+'/'+name,timeout=2)
            self.assertEqual(404,error.exception.code)


    def test_video_ranges_support_seek_suffix_and_if_range(self):
        payload = b'0123456789'
        (self.dist/'clip.mp4').write_bytes(payload)
        url = self.url+'/clip.mp4'
        with urlopen(url) as response:
            etag = response.headers['ETag']
            self.assertEqual('bytes', response.headers['Accept-Ranges'])
        for requested, expected, bounds in [('bytes=2-5', b'2345', '2-5'),
                                             ('bytes=7-', b'789', '7-9'),
                                             ('bytes=-3', b'789', '7-9')]:
            with self.subTest(requested=requested), urlopen(Request(url, headers={'Range':requested, 'If-Range':etag})) as response:
                self.assertEqual(206, response.status)
                self.assertEqual('bytes '+bounds+'/10', response.headers['Content-Range'])
                self.assertEqual(str(len(expected)), response.headers['Content-Length'])
                self.assertEqual(expected, response.read())
        with urlopen(Request(url, headers={'Range':'bytes=2-5', 'If-Range':'"stale"'})) as response:
            self.assertEqual(200, response.status)
            self.assertEqual(payload, response.read())
        with urlopen(Request(url, headers={'Range':'bytes=2-5'}, method='HEAD')) as response:
            self.assertEqual(200, response.status)
            self.assertEqual('10', response.headers['Content-Length'])
            self.assertEqual(b'', response.read())

    def test_video_unsatisfiable_range_and_conditional_request(self):
        (self.dist/'clip.mp4').write_bytes(b'0123456789')
        url = self.url+'/clip.mp4'
        for requested in ['bytes=30-', 'bytes=9-3', 'bytes=-0']:
            with self.subTest(requested=requested), self.assertRaises(HTTPError) as error:
                urlopen(Request(url, headers={'Range':requested}))
            self.assertEqual(416, error.exception.code)
            self.assertEqual('bytes */10', error.exception.headers['Content-Range'])
            self.assertEqual(b'', error.exception.read())
        with urlopen(url) as response:
            etag = response.headers['ETag']
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url, headers={'Range':'bytes=2-5', 'If-None-Match':etag}))
        self.assertEqual(304, error.exception.code)
        self.assertEqual(b'', error.exception.read())
