"""
health_server.py — HTTP server para Railway healthcheck
Corre en background thread
"""
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

import config as C

log = logging.getLogger(__name__)

_status = {"running": True, "last_cycle": "—", "cycles": 0, "errors": 0}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            f"QF×JP Bot v3 — OK\n"
            f"Last cycle: {_status['last_cycle']}\n"
            f"Cycles: {_status['cycles']}\n"
            f"Errors: {_status['errors']}\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silenciar logs HTTP


def update_status(last_cycle: str = None, error: bool = False):
    if last_cycle:
        _status["last_cycle"] = last_cycle
        _status["cycles"] += 1
    if error:
        _status["errors"] += 1


def start_health_server():
    server = HTTPServer(("0.0.0.0", C.PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info(f"Health server en puerto {C.PORT}")
