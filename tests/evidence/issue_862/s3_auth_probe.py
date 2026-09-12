#!/usr/bin/env python3
"""Verify that issue #862 S3 candidates reject invalid credentials.

A valid client first writes and reads a deterministic sentinel so connectivity is
known-good. A second client then attempts to read the same object with invalid
credentials and must receive an HTTP 4xx response. No credentials are emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re

from s3_contract_probe import S3ProbeError, SigV4S3Client, _payload


def _valid_client(args: argparse.Namespace) -> SigV4S3Client:
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise S3ProbeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required")
    return SigV4S3Client(
        args.endpoint,
        access_key,
        secret_key,
        region=args.region,
        timeout=args.timeout,
    )


def run(args: argparse.Namespace) -> dict[str, object]:
    valid = _valid_client(args)
    key = f"issue-862/{args.backend}/{args.run_id}/auth-sentinel.bin"
    data = _payload(f"{args.backend}:{args.run_id}:auth", args.payload_bytes)
    expected = hashlib.sha256(data).hexdigest()

    valid.put_object(args.bucket, key, data)
    try:
        received = valid.get_object(args.bucket, key).body
        if hashlib.sha256(received).hexdigest() != expected:
            raise S3ProbeError("valid credential control read failed SHA-256 verification")

        invalid = SigV4S3Client(
            args.endpoint,
            "I862INVALIDACCESSKEY",
            "issue862-invalid-secret-not-a-real-credential",
            region=args.region,
            timeout=args.timeout,
        )
        try:
            invalid.get_object(args.bucket, key)
        except S3ProbeError as exc:
            match = re.search(r"HTTP (\d{3})", str(exc))
            if not match:
                raise S3ProbeError(
                    "invalid credential request failed without an HTTP authentication response"
                ) from exc
            status = int(match.group(1))
            if not 400 <= status < 500:
                raise S3ProbeError(
                    f"invalid credential request returned non-4xx HTTP status {status}"
                ) from exc
        else:
            raise S3ProbeError("invalid credentials unexpectedly read a protected object")
    finally:
        valid.delete_object(args.bucket, key)

    return {
        "schema_version": 1,
        "issue": 862,
        "backend": args.backend,
        "status": "pass",
        "valid_control_sha256_verified": True,
        "invalid_credentials_rejected_with_4xx": True,
        "credentials_emitted": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=("rustfs", "garage", "seaweedfs"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--payload-bytes", type=int, default=64 * 1024)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run(args)
    except (S3ProbeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "issue": 862,
                    "backend": args.backend,
                    "status": "fail",
                    "error": str(exc),
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
