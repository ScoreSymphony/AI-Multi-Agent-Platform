"""Controlled DNS rebinding fixture for issue #859 runtime evidence.

The fixture answers the first A query for one configured hostname with a
synthetic public address and subsequent A queries with a blocked loopback
address. A small HTTP control surface exposes query counts and answers so the
Bifrost integration lane can prove that every new dial revalidates DNS and
that a rebound private answer is rejected before connection.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socketserver
import struct
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar


class _DNSState:
    def __init__(self, *, hostname: str, first_ip: str, rebound_ip: str) -> None:
        self.hostname = hostname.rstrip(".").casefold()
        self.first_ip = first_ip
        self.rebound_ip = rebound_ip
        self._a_queries = 0
        self._aaaa_queries = 0
        self._answers: list[str] = []
        self._lock = threading.Lock()

    def answer_a(self) -> str:
        with self._lock:
            self._a_queries += 1
            answer = self.first_ip if self._a_queries == 1 else self.rebound_ip
            self._answers.append(answer)
            return answer

    def record_aaaa(self) -> None:
        with self._lock:
            self._aaaa_queries += 1

    def reset(self) -> None:
        with self._lock:
            self._a_queries = 0
            self._aaaa_queries = 0
            self._answers.clear()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "hostname": self.hostname,
                "first_ip": self.first_ip,
                "rebound_ip": self.rebound_ip,
                "a_queries": self._a_queries,
                "aaaa_queries": self._aaaa_queries,
                "a_answers": list(self._answers),
            }


def _decode_question(packet: bytes) -> tuple[str, int, int, int]:
    if len(packet) < 12:
        raise ValueError("DNS packet is shorter than the fixed header")
    offset = 12
    labels: list[str] = []
    while True:
        if offset >= len(packet):
            raise ValueError("truncated DNS qname")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0:
            raise ValueError("compressed DNS questions are not supported")
        if offset + length > len(packet):
            raise ValueError("truncated DNS label")
        labels.append(packet[offset : offset + length].decode("ascii"))
        offset += length
    if offset + 4 > len(packet):
        raise ValueError("truncated DNS question type/class")
    qtype, qclass = struct.unpack("!HH", packet[offset : offset + 4])
    return ".".join(labels).casefold(), qtype, qclass, offset + 4


def _dns_response(packet: bytes, state: _DNSState) -> bytes:
    transaction_id = packet[:2]
    try:
        hostname, qtype, qclass, question_end = _decode_question(packet)
    except (UnicodeDecodeError, ValueError):
        return transaction_id + struct.pack("!HHHHH", 0x8181, 0, 0, 0, 0)

    question = packet[12:question_end]
    is_target = hostname == state.hostname and qclass == 1
    answer = b""
    answer_count = 0

    if is_target and qtype == 1:
        ip = ipaddress.IPv4Address(state.answer_a()).packed
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 0, len(ip)) + ip
        answer_count = 1
    elif is_target and qtype == 28:
        state.record_aaaa()

    header = transaction_id + struct.pack("!HHHHH", 0x8180, 1, answer_count, 0, 0)
    return header + question + answer


class _DNSHandler(socketserver.BaseRequestHandler):
    state: ClassVar[_DNSState]

    def handle(self) -> None:
        packet, sock = self.request
        sock.sendto(_dns_response(packet, self.state), self.client_address)


class _ControlHandler(BaseHTTPRequestHandler):
    server_version = "Issue859RebindingDNS/1.0"
    state: ClassVar[_DNSState]

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"healthy": True})
            return
        if self.path == "/stats":
            self._json(HTTPStatus.OK, self.state.snapshot())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/reset":
            self.state.reset()
            self._json(HTTPStatus.OK, self.state.snapshot())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the issue #859 rebinding DNS fixture")
    parser.add_argument("--dns-host", default="127.0.0.1")
    parser.add_argument("--dns-port", type=int, default=53)
    parser.add_argument("--control-host", default="127.0.0.1")
    parser.add_argument("--control-port", type=int, default=18002)
    parser.add_argument("--hostname", default="issue859-rebind.test")
    parser.add_argument("--first-ip", default="203.0.113.10")
    parser.add_argument("--rebound-ip", default="127.0.0.1")
    args = parser.parse_args(argv)

    state = _DNSState(
        hostname=args.hostname,
        first_ip=str(ipaddress.IPv4Address(args.first_ip)),
        rebound_ip=str(ipaddress.IPv4Address(args.rebound_ip)),
    )
    _DNSHandler.state = state
    _ControlHandler.state = state

    dns_server = socketserver.ThreadingUDPServer((args.dns_host, args.dns_port), _DNSHandler)
    control_server = ThreadingHTTPServer((args.control_host, args.control_port), _ControlHandler)
    control_thread = threading.Thread(target=control_server.serve_forever, daemon=True)
    control_thread.start()
    try:
        dns_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dns_server.shutdown()
        dns_server.server_close()
        control_server.shutdown()
        control_server.server_close()
        control_thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
