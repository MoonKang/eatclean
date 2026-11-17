#!/usr/bin/env python3
"""Serve the web/ folder locally for development: python3 serve.py [port]"""
import functools
import http.server
import os
import sys

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WEB)
print("serving %s on http://localhost:%d" % (WEB, port))
http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
