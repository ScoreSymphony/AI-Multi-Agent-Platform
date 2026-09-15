"""Verification construction seams for single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.verification import (
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
)

from ..config import SingleNodeConfig


@dataclass(frozen=True, slots=True)
class VerificationBundle:
    """Kernel-independent Verification service and canonical completion authority."""

    service: SqliteVerificationService
    completion_authority: SqliteVerificationCompletionAuthority
    database_path: Path


def build_verification(config: SingleNodeConfig) -> VerificationBundle:
    """Build Verification before the kernel so completion authority stays a public dependency."""

    verification_path = config.database_dir / "verification.sqlite3"
    service = SqliteVerificationService(
        verification_path,
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    completion_authority = SqliteVerificationCompletionAuthority(service, verification_path)
    return VerificationBundle(
        service=service,
        completion_authority=completion_authority,
        database_path=verification_path,
    )
