#!/usr/bin/env python3
"""Serve only the Tacto landing page, with compression and revalidation."""
import argparse
from datetime import timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
import gzip
import hashlib
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import stat
from urllib.parse import unquote, urlsplit

DIRECTORY = (Path(__file__).resolve().parent / 'dist').resolve()
MAX_ASSET_BYTES = 4 * 1024 * 1024
MAX_CACHED_BYTES = 512 * 1024
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; "
       "object-src 'none'; frame-ancestors 'none'")
COMPRESSIBLE = {'.html', '.css', '.js', '.svg', '.json', '.txt'}


def accepts_gzip(header):
    qualities = {}
    for item in header.lower().split(','):
        parts = [part.strip() for part in item.split(';')]
        if not parts[0]:
            continue
        quality = 1.0
        for part in parts[1:]:
            if part.startswith('q='):
                try:
                    quality = float(part[2:])
                except ValueError:
                    quality = 0
        qualities[parts[0]] = quality if 0 <= quality <= 1 else 0
    return qualities.get('gzip', qualities.get('*', 0)) > 0


def read_representation(path, stamp, compress):
    # stamp participates in the cache key, including ctime/inode so atomic
    # releases and same-size edits with preserved mtime invalidate the entry.
    del stamp
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ASSET_BYTES:
            raise ValueError('not a bounded regular asset')
        raw = stream.read(MAX_ASSET_BYTES + 1)
    if len(raw) > MAX_ASSET_BYTES:
        raise ValueError('asset exceeds preview size limit')
    body, encoding = raw, None
    if compress and len(raw) >= 512:
        candidate = gzip.compress(raw, compresslevel=6, mtime=0)
        if len(candidate) < len(raw):
            body, encoding = candidate, 'gzip'
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    return body, encoding, etag


cached_representation = lru_cache(maxsize=32)(read_representation)


class StaticHandler(SimpleHTTPRequestHandler):
    server_version = 'TactoStatic'
    sys_version = ''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)

    def translate_path(self, path):
        invalid = str(DIRECTORY / '__not_found__')
        parts = Path(unquote(urlsplit(path).path)).parts
        if any(part.startswith('.') for part in parts if part != '/'):
            return invalid
        target = Path(super().translate_path(path))
        try:
            target.relative_to(DIRECTORY)
            if not target.resolve().is_relative_to(DIRECTORY):
                return invalid
            current = target
            while current != DIRECTORY:
                if current.is_symlink():
                    return invalid
                current = current.parent
        except (ValueError, OSError, RuntimeError):
            return invalid
        return str(target)

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND, 'Not found')
        return None

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            url = urlsplit(self.path).path.rstrip('/') + '/index.html'
            path = Path(self.translate_path(url))
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ASSET_BYTES:
                raise ValueError('not a bounded regular asset')
            compress = path.suffix.lower() in COMPRESSIBLE and accepts_gzip(
                self.headers.get('Accept-Encoding', ''))
            stamp = (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)
            reader = cached_representation if info.st_size <= MAX_CACHED_BYTES else read_representation
            body, encoding, etag = reader(str(path), stamp, compress)
        except (OSError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND, 'Not found')
            return None

        not_modified = False
        condition = self.headers.get('If-None-Match')
        if condition is not None:
            candidates = [value.strip() for value in condition.split(',')]
            not_modified = any(value == '*' or value.removeprefix('W/') == etag
                               for value in candidates)
        elif self.headers.get('If-Modified-Since'):
            try:
                since = parsedate_to_datetime(self.headers['If-Modified-Since'])
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                not_modified = int(info.st_mtime) <= since.timestamp()
            except (ValueError, TypeError, OverflowError):
                pass
        self.send_response(HTTPStatus.NOT_MODIFIED if not_modified else HTTPStatus.OK)
        self.send_header('ETag', etag)
        self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Last-Modified', self.date_time_string(info.st_mtime))
        if encoding:
            self.send_header('Content-Encoding', encoding)
        if not not_modified:
            self.send_header('Content-Type', self.guess_type(str(path)))
            self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        return None if not_modified else io.BytesIO(body)

    def end_headers(self):
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        # Validate changing filenames on every visit; never cache a stale logo
        # indefinitely. A matching ETag returns 304 with no response body.
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', help='Local interface address')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1..65535')
    with ThreadingHTTPServer((args.host, args.port), StaticHandler) as server:
        print('Tacto landing: http://{}:{}'.format(args.host, args.port), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
