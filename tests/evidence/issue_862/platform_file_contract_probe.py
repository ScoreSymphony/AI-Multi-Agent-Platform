#!/usr/bin/env python3
"""Run the platform's canonical FileProvider conformance helper against S3 content."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from s3_contract_probe import S3ProbeError, SigV4S3Client

from ai_multi_agent_platform.contracts import (
    FileProvider,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.testing import assert_file_provider_contract


class S3EvidenceFileProvider(FileProvider):
    """Minimal test adapter proving the provider seam over the candidate S3 subset."""

    def __init__(
        self,
        client: SigV4S3Client,
        *,
        backend: str,
        bucket: str,
        run_id: str,
    ) -> None:
        self._client = client
        self._backend = backend
        self._bucket = bucket
        self._prefix = f"issue-862/{backend}/{run_id}/platform-contract/"
        self._keys: dict[str, str] = {}
        self._descriptor = ProviderDescriptor(
            provider_id=f"issue-862-{backend}-files",
            provider_type="file",
            supported_operations=("write", "read"),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def _key(self, object_ref: str) -> str:
        return f"{self._prefix}{object_ref}"

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        del context
        key = self._key(object_ref)
        self._client.put_object(self._bucket, key, data)
        self._keys[object_ref] = key
        stored_metadata = dict(metadata or {})
        stored_metadata["size"] = len(data)
        return StoredObject(object_ref=object_ref, metadata=stored_metadata)

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        del context
        key = self._keys.get(object_ref, self._key(object_ref))
        return self._client.get_object(self._bucket, key).body

    def cleanup(self) -> None:
        for key in tuple(self._keys.values()):
            self._client.delete_object(self._bucket, key)
        self._keys.clear()


async def run(args: argparse.Namespace) -> dict[str, object]:
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
    provider = S3EvidenceFileProvider(
        client,
        backend=args.backend,
        bucket=args.bucket,
        run_id=args.run_id,
    )
    try:
        await assert_file_provider_contract(
            provider,
            OperationContext(correlation_id=f"issue-862:{args.backend}:{args.run_id}"),
        )
    finally:
        provider.cleanup()

    return {
        "schema_version": 1,
        "issue": 862,
        "backend": args.backend,
        "run_id": args.run_id,
        "status": "pass",
        "canonical_helper": "ai_multi_agent_platform.testing.assert_file_provider_contract",
        "provider_id": provider.descriptor.provider_id,
        "provider_operations": list(provider.descriptor.supported_operations),
        "credentials_emitted": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=("rustfs", "garage", "seaweedfs"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except (S3ProbeError, ValueError, AssertionError) as exc:
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
