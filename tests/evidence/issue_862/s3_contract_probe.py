#!/usr/bin/env python3
"""Dependency-free S3 subset probe for issue #862.

The probe deliberately exercises only the object operations required by the
platform File/Artifact boundary. Credentials are read from the environment and
are never emitted in the JSON evidence output.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class S3Response:
    status: int
    headers: dict[str, str]
    body: bytes


class S3ProbeError(RuntimeError):
    pass


class SigV4S3Client:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        region: str = "us-east-1",
        session_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("endpoint must be an absolute http(s) URL")
        if parsed.path not in {"", "/"}:
            raise ValueError("endpoint must not contain a path")
        self.endpoint = endpoint.rstrip("/")
        self.host = parsed.netloc
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.session_token = session_token
        self.timeout = timeout

    @staticmethod
    def _sha256_hex(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _hmac(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    @staticmethod
    def _quote(value: str, *, safe: str = "-_.~") -> str:
        return urllib.parse.quote(value, safe=safe)

    def _canonical_query(self, query: list[tuple[str, str]]) -> str:
        encoded = [(self._quote(key), self._quote(value)) for key, value in query]
        encoded.sort()
        return "&".join(f"{key}={value}" for key, value in encoded)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
        payload: bytes = b"",
    ) -> S3Response:
        query = query or []
        extra_headers = {key.lower(): value.strip() for key, value in (headers or {}).items()}
        now = dt.datetime.now(dt.UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = self._sha256_hex(payload)

        canonical_uri = self._quote(path, safe="/-_.~")
        canonical_query = self._canonical_query(query)
        signed = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            **extra_headers,
        }
        if self.session_token:
            signed["x-amz-security-token"] = self.session_token

        canonical_headers = "".join(f"{key}:{signed[key]}\n" for key in sorted(signed))
        signed_headers = ";".join(sorted(signed))
        canonical_request = "\n".join(
            [
                method,
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                self._sha256_hex(canonical_request.encode("utf-8")),
            ]
        )
        date_key = self._hmac(("AWS4" + self.secret_key).encode("utf-8"), date_stamp)
        region_key = self._hmac(date_key, self.region)
        service_key = self._hmac(region_key, "s3")
        signing_key = self._hmac(service_key, "aws4_request")
        signature = hmac.new(
            signing_key,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        request_headers = {key: value for key, value in signed.items() if key != "host"}
        request_headers["Host"] = self.host
        request_headers["Authorization"] = authorization
        url = f"{self.endpoint}{canonical_uri}"
        if canonical_query:
            url = f"{url}?{canonical_query}"
        request = urllib.request.Request(
            url,
            data=payload if method in {"PUT", "POST"} else None,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return S3Response(
                    status=response.status,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read()
            detail = body.decode("utf-8", errors="replace")[:1000]
            raise S3ProbeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise S3ProbeError(f"{method} {path} failed: {exc.reason}") from exc

    def create_bucket(self, bucket: str) -> S3Response:
        return self._request("PUT", f"/{bucket}")

    def put_object(self, bucket: str, key: str, data: bytes) -> S3Response:
        return self._request("PUT", f"/{bucket}/{key}", payload=data)

    def head_object(self, bucket: str, key: str) -> S3Response:
        return self._request("HEAD", f"/{bucket}/{key}")

    def get_object(self, bucket: str, key: str, *, byte_range: str | None = None) -> S3Response:
        headers = {"range": byte_range} if byte_range else None
        return self._request("GET", f"/{bucket}/{key}", headers=headers)

    def list_objects(self, bucket: str, *, prefix: str) -> tuple[str, ...]:
        response = self._request(
            "GET",
            f"/{bucket}",
            query=[("list-type", "2"), ("prefix", prefix)],
        )
        try:
            root = ET.fromstring(response.body)
        except ET.ParseError as exc:
            raise S3ProbeError("ListObjectsV2 returned invalid XML") from exc
        return tuple(element.text or "" for element in root.findall(".//{*}Key"))

    def delete_object(self, bucket: str, key: str) -> S3Response:
        return self._request("DELETE", f"/{bucket}/{key}")


def _payload(label: str, size: int) -> bytes:
    seed = hashlib.sha256(label.encode("utf-8")).digest()
    repeats = (size + len(seed) - 1) // len(seed)
    return (seed * repeats)[:size]


def _timed(operation: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = operation()
    return result, time.perf_counter() - started


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise S3ProbeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required")

    client = SigV4S3Client(
        args.endpoint,
        access_key,
        secret_key,
        region=args.region,
        session_token=os.environ.get("AWS_SESSION_TOKEN"),
        timeout=args.timeout,
    )
    run_id = args.run_id or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"issue-862/{args.backend}/{run_id}/"
    key = f"{prefix}primary.bin"
    data = _payload(f"{args.backend}:{run_id}:primary", args.payload_bytes)
    checksum = hashlib.sha256(data).hexdigest()
    created_keys: list[str] = []
    timings: dict[str, float] = {}

    if args.create_bucket:
        _, timings["create_bucket_seconds"] = _timed(lambda: client.create_bucket(args.bucket))

    try:
        response, timings["put_seconds"] = _timed(lambda: client.put_object(args.bucket, key, data))
        if response.status not in {200, 201}:
            raise S3ProbeError(f"PutObject returned unexpected status {response.status}")
        created_keys.append(key)

        head, timings["head_seconds"] = _timed(lambda: client.head_object(args.bucket, key))
        content_length = int(head.headers.get("content-length", "-1"))
        if content_length != len(data):
            raise S3ProbeError(f"HeadObject length {content_length} != expected {len(data)}")

        full, timings["get_seconds"] = _timed(lambda: client.get_object(args.bucket, key))
        if hashlib.sha256(full.body).hexdigest() != checksum:
            raise S3ProbeError("GetObject SHA-256 mismatch")

        range_end = min(len(data), args.range_bytes) - 1
        partial, timings["range_get_seconds"] = _timed(
            lambda: client.get_object(args.bucket, key, byte_range=f"bytes=0-{range_end}")
        )
        if partial.body != data[: range_end + 1]:
            raise S3ProbeError("ranged GetObject payload mismatch")

        listed, timings["list_seconds"] = _timed(
            lambda: client.list_objects(args.bucket, prefix=prefix)
        )
        if key not in listed:
            raise S3ProbeError("ListObjectsV2 did not return the primary object")

        def round_trip(index: int) -> dict[str, Any]:
            concurrent_key = f"{prefix}concurrent-{index:03d}.bin"
            concurrent_data = _payload(
                f"{args.backend}:{run_id}:concurrent:{index}",
                args.concurrent_payload_bytes,
            )
            expected = hashlib.sha256(concurrent_data).hexdigest()
            client.put_object(args.bucket, concurrent_key, concurrent_data)
            received = client.get_object(args.bucket, concurrent_key).body
            actual = hashlib.sha256(received).hexdigest()
            if actual != expected:
                raise S3ProbeError(f"concurrent object {index} SHA-256 mismatch")
            return {"key": concurrent_key, "sha256": expected, "bytes": len(concurrent_data)}

        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            concurrent_results = list(executor.map(round_trip, range(args.concurrency)))
        timings["concurrent_round_trip_seconds"] = time.perf_counter() - started
        created_keys.extend(item["key"] for item in concurrent_results)

        delete_started = time.perf_counter()
        for created_key in reversed(created_keys):
            client.delete_object(args.bucket, created_key)
        timings["delete_all_seconds"] = time.perf_counter() - delete_started
        created_keys.clear()

        safe_endpoint = urllib.parse.urlsplit(args.endpoint)
        return {
            "schema_version": 1,
            "issue": 862,
            "backend": args.backend,
            "run_id": run_id,
            "endpoint": f"{safe_endpoint.scheme}://{safe_endpoint.netloc}",
            "region": args.region,
            "bucket": args.bucket,
            "operations": {
                "PutObject": "pass",
                "HeadObject": "pass",
                "GetObject": "pass",
                "RangeGetObject": "pass",
                "ListObjectsV2": "pass",
                "DeleteObject": "pass",
                "concurrent_round_trip": "pass",
            },
            "primary": {
                "key": key,
                "bytes": len(data),
                "sha256": checksum,
                "etag_observed": head.headers.get("etag"),
                "etag_used_as_canonical_checksum": False,
            },
            "concurrency": args.concurrency,
            "concurrent_objects": concurrent_results,
            "timings_seconds": timings,
            "credentials_emitted": False,
        }
    finally:
        cleanup_errors: list[str] = []
        for created_key in reversed(created_keys):
            try:
                client.delete_object(args.bucket, created_key)
            except S3ProbeError as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            print(json.dumps({"cleanup_errors": cleanup_errors}, indent=2), file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=("rustfs", "garage", "seaweedfs"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--run-id")
    parser.add_argument("--create-bucket", action="store_true")
    parser.add_argument("--payload-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--range-bytes", type=int, default=4096)
    parser.add_argument("--concurrent-payload-bytes", type=int, default=128 * 1024)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        evidence = run_probe(args)
    except (S3ProbeError, ValueError) as exc:
        print(
            json.dumps({"issue": 862, "backend": args.backend, "status": "fail", "error": str(exc)})
        )
        return 1
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
