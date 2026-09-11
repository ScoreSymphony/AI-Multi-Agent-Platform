from __future__ import annotations

import argparse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-host", default="127.0.0.2")
    parser.add_argument("--source-port", required=True, type=int)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", required=True, type=int)
    parser.add_argument("--marker", required=True)
    return parser.parse_args()


def main() -> None:
    args = _args()
    marker = Path(args.marker)
    target_url = f"http://{args.target_host}:{args.target_port}/private"

    class SourceHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/start":
                self.send_error(404)
                return
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        def log_message(self, _format: str, *args: object) -> None:
            return

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            marker.write_text("private target reached\n", encoding="utf-8")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"private-target")

        def log_message(self, _format: str, *args: object) -> None:
            return

    source = ThreadingHTTPServer((args.source_host, args.source_port), SourceHandler)
    target = ThreadingHTTPServer((args.target_host, args.target_port), TargetHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    try:
        source.serve_forever()
    finally:
        source.server_close()
        target.shutdown()
        target.server_close()
        target_thread.join(timeout=2)


if __name__ == "__main__":
    main()
