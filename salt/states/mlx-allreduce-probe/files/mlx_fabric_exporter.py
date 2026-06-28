#!/usr/bin/env python3
"""Always-on exporter that serves the AllReduce probe's Prometheus textfile.

The probe (mlx_allreduce_probe.py) runs periodically under mlx.launch and writes a
.prom textfile. This tiny server exposes that file at /metrics for the OTEL agent,
plus a staleness gauge so alerts can fire if the probe stops running. Stdlib only.
"""
import argparse
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def build(textfile, max_age):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            return

        def do_GET(self):
            if self.path.rstrip("/") not in ("/metrics", ""):
                self.send_response(404)
                self.end_headers()
                return
            body = b""
            stale = 1
            if os.path.exists(textfile):
                age = time.time() - os.path.getmtime(textfile)
                stale = 1 if age > max_age else 0
                with open(textfile, "rb") as fh:
                    body = fh.read()
            body += (
                b"# HELP mlx_allreduce_probe_stale 1 if textfile missing or older "
                b"than max_age.\n# TYPE mlx_allreduce_probe_stale gauge\n"
                b"mlx_allreduce_probe_stale %d\n" % stale
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--textfile", default="/var/lib/hydra/textfile/mlx_allreduce.prom")
    ap.add_argument("--port", type=int, default=11503)
    ap.add_argument("--max-age", type=int, default=300,
                    help="seconds before the probe result is considered stale")
    args = ap.parse_args()
    ThreadingHTTPServer(("0.0.0.0", args.port),
                        build(args.textfile, args.max_age)).serve_forever()


if __name__ == "__main__":
    main()
