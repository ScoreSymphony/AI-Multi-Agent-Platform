#!/usr/bin/env python3
"""Exercise Garage metadata snapshot/restore semantics for issue #862.

The workflow around this probe invokes Garage's native ``meta snapshot --all``
command and restores the SQLite database while Garage is stopped. This probe
creates deterministic object states before/after the snapshot and validates the
restored metadata view without ever emitting credentials.
"""

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


def _key(args: argparse.Namespace, state: str) -> str:
    return f"issue-862/garage/{args.run_id}/snapshot-{state}.bin"


def _data(args: argparse.Namespace, state: str) -> bytes:
    return _payload(f"garage:{args.run_id}:snapshot:{state}", args.payload_bytes)


def _put_and_verify(args: argparse.Namespace, state: str) -> dict[str, object]:
    client = _client(args)
    key = _key(args, state)
    data = _data(args, state)
    expected = hashlib.sha256(data).hexdigest()
    client.put_object(args.bucket, key, data)
    received = client.get_object(args.bucket, key).body
    actual = hashlib.sha256(received).hexdigest()
    if actual != expected:
        raise S3ProbeError(f"{state} object failed immediate SHA-256 verification")
    return {
        "issue": 862,
        "backend": "garage",
        "phase": state,
        "key": key,
        "bytes": len(data),
        "sha256": expected,
        "status": "pass",
        "credentials_emitted": False,
    }


def seed(args: argparse.Namespace) -> dict[str, object]:
    return _put_and_verify(args, "pre-snapshot")


def mutate(args: argparse.Namespace) -> dict[str, object]:
    return _put_and_verify(args, "post-snapshot")


def verify(args: argparse.Namespace) -> dict[str, object]:
    client = _client(args)

    stable_key = _key(args, "pre-snapshot")
    stable_data = _data(args, "pre-snapshot")
    expected = hashlib.sha256(stable_data).hexdigest()
    received = client.get_object(args.bucket, stable_key).body
    actual = hashlib.sha256(received).hexdigest()
    if actual != expected:
        raise S3ProbeError("pre-snapshot object SHA-256 mismatch after metadata restore")

    post_key = _key(args, "post-snapshot")
    post_snapshot_absent = False
    try:
        client.get_object(args.bucket, post_key)
    except S3ProbeError as exc:
        if "HTTP 404" in str(exc) or "NoSuchKey" in str(exc):
            post_snapshot_absent = True
        else:
            raise
    if not post_snapshot_absent:
        raise S3ProbeError("post-snapshot object remained visible after metadata restore")

    client.delete_object(args.bucket, stable_key)
    return {
        "issue": 862,
        "backend": "garage",
        "phase": "post-restore-verify",
        "status": "pass",
        "pre_snapshot_sha256_verified": True,
        "post_snapshot_state_absent": True,
        "credentials_emitted": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "mutate", "verify"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--region", default="garage")
    parser.add_argument("--payload-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        operation = {"seed": seed, "mutate": mutate, "verify": verify}[args.phase]
        result = operation(args)
    except (S3ProbeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "issue": 862,
                    "backend": "garage",
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
