"""Controlled HTTP sentinel for issue #859 Bifrost SSRF runtime evidence.

The sentinel is intentionally local and synthetic. It records whether a configured
URL-fetching path actually reaches a blocked target, allowing deployment-level SSRF
tests to prove rejection before connection without probing real metadata services or
unrelated private infrastructure.
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar


class _SentinelState:
    def __init__(self) -> None:
        self._paths: Counter[str] = Counter()
        self._lock = threading.Lock()

    def record_hit(self, path: str) -> int:
        with self._lock:
            self._paths[path] += 1
            return self._paths[path]

    def reset(self) -> None:
        with self._lock:
            self._paths.clear()

    def hits(self) -> int:
        with self._lock:
            return sum(self._paths.values())

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._paths)


class _Handler(BaseHTTPRequestHandler):
    server_version = "Issue859SSRFSentinel/1.2"
    protocol_version = "HTTP/1.1"
    state: ClassVar[_SentinelState] = _SentinelState()
    rebinding_location: ClassVar[str] = (
        "http://issue859-rebind.test:18003/ssrf-sentinel.txt"
    )

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"healthy": True})
            return
        if self.path == "/control/hits":
            self._json(HTTPStatus.OK, {"hits": self.state.hits()})
            return
        if self.path == "/control/stats":
            self._json(
                HTTPStatus.OK,
                {"hits": self.state.hits(), "paths": self.state.stats()},
            )
            return
        if self.path == "/ssrf-sentinel.txt":
            self.state.record_hit(self.path)
            self._text(HTTPStatus.OK, "issue-859-controlled-ssrf-sentinel\n")
            return
        if self.path == "/redirect-to-loopback":
            self.state.record_hit(self.path)
            self._redirect("http://127.0.0.1:18001/ssrf-sentinel.txt")
            return
        if self.path == "/redirect-to-rebound-host":
            self.state.record_hit(self.path)
            self._redirect(self.rebinding_location)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/control/reset":
            self.state.reset()
            self._json(HTTPStatus.OK, {"hits": 0})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()

    def _text(self, status: HTTPStatus, payload: str) -> None:
        encoded = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the issue #859 controlled SSRF sentinel")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument(
        "--rebinding-location",
        default="http://issue859-rebind.test:18003/ssrf-sentinel.txt",
    )
    args = parser.parse_args(argv)

    _Handler.rebinding_location = args.rebinding_location
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
