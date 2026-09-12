"""Hermetic OpenAI-compatible fixture for the pinned Bifrost CI smoke campaign.

This server is intentionally tiny. It exercises request/response translation, SSE
streaming, structured JSON, tool-call pass-through, and a controlled unavailable
upstream mode without external model APIs or credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar


class _State:
    unavailable = False


class _Handler(BaseHTTPRequestHandler):
    server_version = "Issue859MockOpenAI/1.0"
    protocol_version = "HTTP/1.1"
    state: ClassVar[_State] = _State()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"healthy": True})
            return
        if self.path == "/v1/models":
            self._json(
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "fixture-model",
                            "object": "model",
                            "created": 0,
                            "owned_by": "issue-859",
                        }
                    ],
                },
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/control/available":
            self.state.unavailable = False
            self._json(HTTPStatus.OK, {"unavailable": False})
            return
        if self.path == "/control/unavailable":
            self.state.unavailable = True
            self._json(HTTPStatus.OK, {"unavailable": True})
            return
        if self.path != "/v1/chat/completions":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        payload = self._request_json()
        if self.state.unavailable:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "error": {
                        "message": "synthetic-secret=must-not-leak",
                        "type": "server_error",
                    }
                },
            )
            return

        if payload.get("stream") is True:
            self._stream_chat(payload)
            return
        self._chat(payload)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _request_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(parsed, dict):
            raise ValueError("request body must be a JSON object")
        return parsed

    def _chat(self, request_payload: dict[str, Any]) -> None:
        model = str(request_payload.get("model") or "fixture-model")
        message: dict[str, Any] = {"role": "assistant", "content": "ready"}
        finish_reason = "stop"

        tools = request_payload.get("tools")
        if isinstance(tools, list) and tools:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_issue859",
                        "type": "function",
                        "function": {
                            "name": "report_status",
                            "arguments": '{"status":"ready"}',
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        elif request_payload.get("response_format") is not None:
            message["content"] = '{"status":"ready"}'

        self._json(
            HTTPStatus.OK,
            {
                "id": "chatcmpl-issue859",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    def _stream_chat(self, request_payload: dict[str, Any]) -> None:
        model = str(request_payload.get("model") or "fixture-model")
        created = int(time.time())
        chunks = (
            {
                "id": "chatcmpl-issue859-stream",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-issue859-stream",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "ready"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-issue859-stream",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
        body = (
            "".join(f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n" for chunk in chunks)
            + "data: [DONE]\n\n"
        )
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()

    def _json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()


def _prepare_github_actions_bifrost_data_root() -> None:
    workspace = os.getenv("GITHUB_WORKSPACE")
    if not workspace:
        return
    data_root = Path(workspace) / ".issue859-bifrost-data"
    data_root.mkdir(parents=True, exist_ok=True)
    # The reviewed image runs as UID 1000:GID 0 and requires APP_DIR to be writable.
    # This is a disposable CI-only directory containing synthetic configuration/state.
    data_root.chmod(0o777)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the issue #859 mock OpenAI endpoint")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args(argv)
    _prepare_github_actions_bifrost_data_root()
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
