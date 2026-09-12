#!/usr/bin/env python3
"""Verify acknowledged S3 data and new writes across a partial cluster failure."""

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
        timeout=args.timeout,
    )


def _object(args: argparse.Namespace, label: str) -> tuple[str, bytes, str]:
    key = f"issue-862/{args.backend}/{args.run_id}/partial-failure-{label}.bin"
    data = _payload(f"{args.backend}:{args.run_id}:partial-failure:{label}", args.payload_bytes)
    return key, data, hashlib.sha256(data).hexdigest()


def _read_verified(client: SigV4S3Client, bucket: str, key: str, expected: str) -> None:
    actual = hashlib.sha256(client.get_object(bucket, key).body).hexdigest()
    if actual != expected:
        raise S3ProbeError(f"SHA-256 mismatch for {key}")


def seed(args: argparse.Namespace) -> dict[str, object]:
    client = _client(args)
    key, data, checksum = _object(args, "before")
    client.put_object(args.bucket, key, data)
    _read_verified(client, args.bucket, key, checksum)
    return {
        "schema_version": 1,
        "issue": 862,
        "backend": args.backend,
        "phase": "pre_failure_seed",
        "status": "pass",
        "key": key,
        "sha256": checksum,
        "bytes": len(data),
        "credentials_emitted": False,
    }


def during_failure(args: argparse.Namespace) -> dict[str, object]:
    client = _client(args)
    before_key, _, before_checksum = _object(args, "before")
    _read_verified(client, args.bucket, before_key, before_checksum)

    during_key, during_data, during_checksum = _object(args, "during")
    client.put_object(args.bucket, during_key, during_data)
    _read_verified(client, args.bucket, during_key, during_checksum)
    return {
        "schema_version": 1,
        "issue": 862,
        "backend": args.backend,
        "phase": "during_partial_failure",
        "status": "pass",
        "pre_failure_object_sha256_verified": True,
        "write_during_failure_sha256_verified": True,
        "credentials_emitted": False,
    }


def recovered(args: argparse.Namespace) -> dict[str, object]:
    client = _client(args)
    verified: list[str] = []
    for label in ("before", "during"):
        key, _, checksum = _object(args, label)
        _read_verified(client, args.bucket, key, checksum)
        verified.append(key)
    for key in verified:
        client.delete_object(args.bucket, key)
    return {
        "schema_version": 1,
        "issue": 862,
        "backend": args.backend,
        "phase": "post_failure_recovery",
        "status": "pass",
        "verified_objects": len(verified),
        "deleted_after_verification": True,
        "credentials_emitted": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "during-failure", "recovered"))
    parser.add_argument("--backend", required=True, choices=("rustfs", "garage", "seaweedfs"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--payload-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    operations = {
        "seed": seed,
        "during-failure": during_failure,
        "recovered": recovered,
    }
    try:
        result = operations[args.phase](args)
    except (OSError, S3ProbeError, ValueError) as exc:
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
