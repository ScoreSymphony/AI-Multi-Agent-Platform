#!/usr/bin/env python3
"""Write or verify a deterministic S3 sentinel across an in-place backend upgrade."""

from __future__ import annotations

import argparse
import hashlib
import json
import os

from s3_contract_probe import S3ProbeError, SigV4S3Client, _payload


def _client(args: argparse.Namespace) -> SigV4S3Client:
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise S3ProbeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required")
    return SigV4S3Client(
        args.endpoint,
        access_key,
        secret_key,
        region=args.region,
        session_token=os.environ.get("AWS_SESSION_TOKEN"),
        timeout=args.timeout,
    )


def _key(args: argparse.Namespace) -> str:
    return f"issue-862/{args.backend}/{args.run_id}/upgrade.bin"


def _data(args: argparse.Namespace) -> bytes:
    return _payload(f"{args.backend}:{args.run_id}:upgrade", args.payload_bytes)


def write(args: argparse.Namespace) -> dict[str, object]:
    client = _client(args)
    if args.create_bucket:
        client.create_bucket(args.bucket)
    key = _key(args)
    data = _data(args)
    expected = hashlib.sha256(data).hexdigest()
    client.put_object(args.bucket, key, data)
    received = client.get_object(args.bucket, key).body
    actual = hashlib.sha256(received).hexdigest()
    if actual != expected:
        raise S3ProbeError("upgrade sentinel failed immediate SHA-256 verification")
    return {
        "schema_version": 1,
        "issue": 862,
        "backend": args.backend,
        "phase": "pre_upgrade_write",
        "key": key,
        "bytes": len(data),
        "sha256": expected,
        "status": "pass",
        "credentials_emitted": False,
    }


def verify(args: argparse.Namespace) -> dict[str, object]:
    client = _client(args)
    key = _key(args)
    data = _data(args)
    expected = hashlib.sha256(data).hexdigest()
    received = client.get_object(args.bucket, key).body
    actual = hashlib.sha256(received).hexdigest()
    if actual != expected:
        raise S3ProbeError("upgrade sentinel SHA-256 mismatch after backend upgrade")
    if args.delete_after_verify:
        client.delete_object(args.bucket, key)
    return {
        "schema_version": 1,
        "issue": 862,
        "backend": args.backend,
        "phase": "post_upgrade_verify",
        "key": key,
        "bytes": len(data),
        "sha256": expected,
        "status": "pass",
        "deleted_after_verification": args.delete_after_verify,
        "credentials_emitted": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("write", "verify"))
    parser.add_argument("--backend", required=True, choices=("rustfs", "garage", "seaweedfs"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--payload-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--create-bucket", action="store_true")
    parser.add_argument("--delete-after-verify", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = write(args) if args.phase == "write" else verify(args)
    except (S3ProbeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "issue": 862,
                    "backend": args.backend,
                    "phase": args.phase,
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
