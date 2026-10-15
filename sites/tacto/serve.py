#!/usr/bin/env python3
"""Serve only the Tacto static landing page on a chosen local interface."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

DIRECTORY = (Path(__file__).resolve().parent / 'dist').resolve()
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; "
       "object-src 'none'; frame-ancestors 'none'")


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
        self.send_error(404, 'Not found')
        return None

    def end_headers(self):
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
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
