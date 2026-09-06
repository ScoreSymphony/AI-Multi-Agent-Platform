"""Replaceable Registry artifact signature verification."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .items import RegistryItem


class RegistrySignatureVerifier(Protocol):
    def verify(self, item: RegistryItem, artifact: bytes) -> bool | None: ...


class HmacSha256SignatureVerifier:
    """Deterministic private/self-hosted signature verifier using trusted keyed digests.

    Registry metadata uses ``hmac-sha256:<hex>`` in ``integrity.signature`` and names the
    deployment-owned trusted key with ``integrity.signature_key_id``. Public registries can
    replace this adapter with an asymmetric verifier without changing DistributionService.
    """

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        normalized: dict[str, bytes] = {}
        for key_id, key in keys.items():
            if not key_id.strip():
                raise ValueError("registry signature key ID must be non-blank")
            if not key:
                raise ValueError("registry signature key material must not be empty")
            normalized[key_id] = bytes(key)
        self._keys = normalized

    def verify(self, item: RegistryItem, artifact: bytes) -> bool | None:
        signature = item.integrity.signature
        if signature is None:
            return None
        key_id = item.integrity.signature_key_id
        if key_id is None:
            return False
        key = self._keys.get(key_id)
        if key is None:
            return False
        algorithm, separator, expected = signature.partition(":")
        if not separator or algorithm != "hmac-sha256" or len(expected) != 64:
            return False
        try:
            bytes.fromhex(expected)
        except ValueError:
            return False
        actual = hmac.new(key, artifact, hashlib.sha256).hexdigest()
        return hmac.compare_digest(actual, expected)


def load_hmac_signature_keys(path: Path) -> dict[str, bytes]:
    """Load deployment-owned key material from a JSON object outside canonical state."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("registry signature key file must be a JSON object")
    result: dict[str, bytes] = {}
    for key_id, value in document.items():
        if not isinstance(key_id, str) or not isinstance(value, str):
            raise ValueError("registry signature key file must map string IDs to string keys")
        if not key_id.strip() or not value:
            raise ValueError("registry signature key IDs and values must be non-empty")
        result[key_id] = value.encode("utf-8")
    return result
