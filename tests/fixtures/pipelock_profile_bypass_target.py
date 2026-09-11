from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _Handler(BaseHTTPRequestHandler):
    marker: Path

    def _record(self, *, body: bytes = b"") -> None:
        record = {
            "method": self.command,
            "path": self.path,
            "body": body.decode("utf-8", errors="replace"),
        }
        with self.marker.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self._record()
        if self.path == "/issue-730-redirect":
            self.send_response(302)
            self.send_header("Location", "/issue-730-redirect-target")
            self.end_headers()
            return
        self._json_response({"ok": True, "path": self.path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self._record(body=body)
        self._json_response({"ok": True, "path": self.path})

    def _json_response(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--marker", required=True, type=Path)
    args = parser.parse_args()
    _Handler.marker = args.marker
    with ThreadingHTTPServer((args.host, args.port), _Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
