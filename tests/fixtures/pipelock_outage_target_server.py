from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SENTINEL = "issue-730-outage-target-ok"


class TargetHandler(BaseHTTPRequestHandler):
    count_file: Path

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/ok":
            self.send_error(404)
            return

        count = int(self.count_file.read_text(encoding="utf-8")) + 1
        self.count_file.write_text(str(count), encoding="utf-8")
        body = SENTINEL.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--count-file", type=Path, required=True)
    args = parser.parse_args()

    args.count_file.write_text("0", encoding="utf-8")
    TargetHandler.count_file = args.count_file
    server = ThreadingHTTPServer(("127.0.0.1", args.port), TargetHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
