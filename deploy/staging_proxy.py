#!/usr/bin/env python3
"""Open the private staging site in your browser.

Forwards http://127.0.0.1:8080 to the staging Cloud Run service, adding your
gcloud sign-in (an identity token) to each request. Only your own machine can
connect, and only read requests (GET/HEAD) are forwarded.

    python3 deploy/staging_proxy.py
    # then open http://localhost:8080

Needs gcloud signed in with an account that can invoke the service (project
owners can). Stop it with Ctrl-C.
"""
import argparse
import http.server
import subprocess
import time
import urllib.error
import urllib.request

SERVICE = "inspection-map-staging"
PASS_HEADERS = ("Content-Type", "Content-Length", "Cache-Control", "Last-Modified", "ETag")


def gcloud(*args):
    return subprocess.run(["gcloud", *args], check=True, capture_output=True, text=True).stdout.strip()


class Token:
    """Identity tokens last an hour; fetch a new one every 45 minutes."""

    def __init__(self):
        self.value, self.expires = None, 0

    def get(self):
        if time.time() > self.expires:
            self.value = gcloud("auth", "print-identity-token")
            self.expires = time.time() + 45 * 60
        return self.value


def make_handler(base_url, token):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _forward(self, send_body):
            req = urllib.request.Request(base_url + self.path, method=self.command,
                                         headers={"Authorization": "Bearer " + token.get()})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status, headers, body = resp.status, resp.headers, resp.read()
            except urllib.error.HTTPError as e:
                status, headers, body = e.code, e.headers, e.read()
            self.send_response(status)
            for h in PASS_HEADERS:
                if headers.get(h) and h != "Content-Length":
                    self.send_header(h, headers[h])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def do_GET(self):
            self._forward(True)

        def do_HEAD(self):
            self._forward(False)

        def log_message(self, fmt, *args):
            pass  # quiet; errors still show as tracebacks

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--region", default="us-east1")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()

    extra = ["--project", args.project] if args.project else []
    url = gcloud("run", "services", "describe", SERVICE, "--region", args.region,
                 "--format=value(status.url)", *extra)
    token = Token()
    token.get()  # fail now, not on the first page load, if gcloud is not signed in
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(url, token))
    print("Staging: http://localhost:%d  (forwarding to %s; Ctrl-C to stop)" % (args.port, url), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
