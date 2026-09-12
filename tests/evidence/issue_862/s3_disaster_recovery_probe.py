#!/usr/bin/env python3
"""Export and restore a checksummed synthetic S3 blob set for issue #862.

This probe evaluates the object-store recovery boundary only. It does not claim
that the platform's current #40 single-node backup implementation automatically
includes external S3 content. Credentials are runtime-only and are never written
to the backup manifest or evidence output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from s3_contract_probe import S3ProbeError, SigV4S3Client, _payload

_SCHEMA_VERSION = 1


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


def _manifest_path(backup_dir: Path) -> Path:
    return backup_dir / "manifest.json"


def _seed_objects(args: argparse.Namespace) -> tuple[tuple[str, bytes], ...]:
    prefix = f"issue-862/{args.backend}/{args.run_id}/disaster-recovery/"
    specs = (
        ("primary.bin", 1024 * 1024),
        ("nested/secondary.bin", 384 * 1024),
        ("small.bin", 4096),
    )
    return tuple(
        (
            f"{prefix}{name}",
            _payload(f"{args.backend}:{args.run_id}:dr:{name}", size),
        )
        for name, size in specs
    )


def export_backup(args: argparse.Namespace) -> dict[str, Any]:
    client = _client(args)
    backup_dir = Path(args.backup_dir).resolve()
    if backup_dir.exists():
        raise S3ProbeError(f"backup directory already exists: {backup_dir}")
    objects_dir = backup_dir / "objects"
    objects_dir.mkdir(parents=True)

    if args.create_bucket:
        client.create_bucket(args.bucket)

    seeded = _seed_objects(args)
    for key, data in seeded:
        client.put_object(args.bucket, key, data)
        actual = client.get_object(args.bucket, key).body
        if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
            raise S3ProbeError(f"seeded object failed immediate SHA-256 verification: {key}")

    prefix = seeded[0][0].rsplit("primary.bin", 1)[0]
    listed = set(client.list_objects(args.bucket, prefix=prefix))
    expected_keys = {key for key, _ in seeded}
    if not expected_keys.issubset(listed):
        missing = sorted(expected_keys - listed)
        raise S3ProbeError(f"ListObjectsV2 omitted seeded backup objects: {missing}")

    entries: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(expected_keys)):
        body = client.get_object(args.bucket, key).body
        relative = f"objects/{index:03d}.bin"
        destination = backup_dir / relative
        destination.write_bytes(body)
        entries.append(
            {
                "key": key,
                "backup_path": relative,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )

    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "issue": 862,
        "backend": args.backend,
        "bucket": args.bucket,
        "run_id": args.run_id,
        "canonical_checksum": "sha256",
        "entries": entries,
        "credentials_emitted": False,
    }
    _manifest_path(backup_dir).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema_version": _SCHEMA_VERSION,
        "issue": 862,
        "backend": args.backend,
        "phase": "logical_blob_export",
        "status": "pass",
        "objects_exported": len(entries),
        "bytes_exported": sum(int(entry["bytes"]) for entry in entries),
        "manifest_sha256": hashlib.sha256(_manifest_path(backup_dir).read_bytes()).hexdigest(),
        "credentials_emitted": False,
    }


def _load_manifest(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    backup_dir = Path(args.backup_dir).resolve()
    manifest_path = _manifest_path(backup_dir)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise S3ProbeError("backup manifest is missing or invalid") from exc
    if not isinstance(raw, dict):
        raise S3ProbeError("backup manifest must be a JSON object")
    if raw.get("schema_version") != _SCHEMA_VERSION or raw.get("issue") != 862:
        raise S3ProbeError("backup manifest identity/version mismatch")
    if raw.get("backend") != args.backend or raw.get("bucket") != args.bucket:
        raise S3ProbeError("backup manifest backend/bucket mismatch")
    if raw.get("run_id") != args.run_id:
        raise S3ProbeError("backup manifest run_id mismatch")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise S3ProbeError("backup manifest entries must be a non-empty list")
    return backup_dir, raw


def restore_backup(args: argparse.Namespace) -> dict[str, Any]:
    client = _client(args)
    backup_dir, manifest = _load_manifest(args)
    entries = manifest["entries"]
    assert isinstance(entries, list)

    if args.create_bucket:
        client.create_bucket(args.bucket)

    first_entry = entries[0]
    if not isinstance(first_entry, dict) or not isinstance(first_entry.get("key"), str):
        raise S3ProbeError("backup manifest first entry key is invalid")
    first_key = first_entry["key"]
    prefix = first_key.rsplit("disaster-recovery/", 1)[0] + "disaster-recovery/"
    pre_restore_keys = client.list_objects(args.bucket, prefix=prefix)
    if pre_restore_keys:
        raise S3ProbeError("replacement store is not empty before restore")

    restored_keys: list[str] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise S3ProbeError("backup manifest entry must be a JSON object")
        key = raw_entry.get("key")
        relative = raw_entry.get("backup_path")
        expected_size = raw_entry.get("bytes")
        expected_sha256 = raw_entry.get("sha256")
        if not isinstance(key, str) or not key:
            raise S3ProbeError("backup manifest entry key is invalid")
        if not isinstance(relative, str) or not relative.startswith("objects/") or ".." in relative:
            raise S3ProbeError("backup manifest entry path is unsafe")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise S3ProbeError("backup manifest entry byte count is invalid")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise S3ProbeError("backup manifest entry SHA-256 is invalid")

        source = backup_dir / relative
        body = source.read_bytes()
        if len(body) != expected_size:
            raise S3ProbeError(f"backup payload size mismatch: {relative}")
        if hashlib.sha256(body).hexdigest() != expected_sha256:
            raise S3ProbeError(f"backup payload SHA-256 mismatch: {relative}")
        client.put_object(args.bucket, key, body)
        restored = client.get_object(args.bucket, key).body
        if hashlib.sha256(restored).hexdigest() != expected_sha256:
            raise S3ProbeError(f"restored object SHA-256 mismatch: {key}")
        restored_keys.append(key)

    listed = set(client.list_objects(args.bucket, prefix=prefix))
    if not set(restored_keys).issubset(listed):
        raise S3ProbeError("restored object set is incomplete in ListObjectsV2")

    return {
        "schema_version": _SCHEMA_VERSION,
        "issue": 862,
        "backend": args.backend,
        "phase": "clean_store_restore",
        "status": "pass",
        "replacement_store_verified_empty_before_restore": True,
        "objects_restored": len(restored_keys),
        "bytes_restored": sum(int(entry["bytes"]) for entry in entries if isinstance(entry, dict)),
        "all_sha256_verified": True,
        "credentials_emitted": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("export", "restore"))
    parser.add_argument("--backend", required=True, choices=("rustfs", "garage", "seaweedfs"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--create-bucket", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = export_backup(args) if args.phase == "export" else restore_backup(args)
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
