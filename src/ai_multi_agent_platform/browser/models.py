"""Canonical browser/web capability models owned by the platform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue, OperationContext
from ai_multi_agent_platform.domain import new_id, validate_id


class BrowserOperation(StrEnum):
    NAVIGATE = "navigate"
    EXTRACT = "extract"
    FOLLOW_LINK = "follow_link"
    SUBMIT_FORM = "submit_form"
    DOWNLOAD = "download"
    CLOSE_SESSION = "close_session"


class BrowserPrivacyClassification(StrEnum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


@dataclass(frozen=True, slots=True)
class BrowserProviderFeatures:
    """Backend-neutral feature metadata used for provider discovery/placement."""

    operations: tuple[BrowserOperation, ...]
    headless: bool
    interactive: bool
    javascript: bool
    file_upload: bool
    file_download: bool
    screenshots: bool
    session_persistence: bool
    proxy_policy: bool
    authentication_mechanisms: tuple[str, ...] = ()
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.operations:
            raise ValueError("browser provider must expose at least one operation")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("browser operations must not contain duplicates")
        if not self.version.strip():
            raise ValueError("browser provider version must not be blank")
        if any(not mechanism.strip() for mechanism in self.authentication_mechanisms):
            raise ValueError("authentication mechanisms must not contain blank values")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "operations": [operation.value for operation in self.operations],
            "headless": self.headless,
            "interactive": self.interactive,
            "javascript": self.javascript,
            "file_upload": self.file_upload,
            "file_download": self.file_download,
            "screenshots": self.screenshots,
            "session_persistence": self.session_persistence,
            "proxy_policy": self.proxy_policy,
            "authentication_mechanisms": list(self.authentication_mechanisms),
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class BrowserSessionScope:
    """Canonical ownership/scope attached to a browser session reference."""

    owner_type: str | None = None
    owner_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if (self.owner_type is None) != (self.owner_id is None):
            raise ValueError("browser session owner_type and owner_id must be set together")
        if self.owner_type is not None and not self.owner_type.strip():
            raise ValueError("browser session owner_type must not be blank")
        if self.owner_id is not None and not self.owner_id.strip():
            raise ValueError("browser session owner_id must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.run_id is not None:
            validate_id(self.run_id, "run")
        if self.agent_id is not None:
            validate_id(self.agent_id, "agent")


@dataclass(frozen=True, slots=True)
class BrowserSessionRef:
    """Backend-neutral reference to reusable browser context.

    Raw cookies, credentials and provider-private page/session identifiers never appear in
    this canonical object. Provider-private diagnostics may only appear in namespaced
    ``adapter_metadata``.
    """

    session_id: str
    scope: BrowserSessionScope
    created_at: datetime
    expires_at: datetime | None = None
    storage_profile_ref: str | None = None
    privacy: BrowserPrivacyClassification = BrowserPrivacyClassification.STANDARD
    allowed_domains: tuple[str, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.session_id, "browser_session")
        _require_aware(self.created_at, "created_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("expires_at must be after created_at")
        if self.storage_profile_ref is not None and not self.storage_profile_ref.strip():
            raise ValueError("storage_profile_ref must not be blank")
        if any(not domain.strip() for domain in self.allowed_domains):
            raise ValueError("allowed_domains must not contain blank values")

    @classmethod
    def create(
        cls,
        operation: OperationContext,
        *,
        scope: BrowserSessionScope | None = None,
        privacy: BrowserPrivacyClassification = BrowserPrivacyClassification.STANDARD,
        allowed_domains: tuple[str, ...] = (),
        expires_at: datetime | None = None,
    ) -> BrowserSessionRef:
        resolved_scope = scope or BrowserSessionScope(
            owner_type=operation.owner_type,
            owner_id=operation.owner_id,
            project_id=operation.project_id,
        )
        if (
            resolved_scope.owner_type is not None
            and operation.owner_type is not None
            and resolved_scope.owner_type != operation.owner_type
        ):
            raise ValueError("browser session owner_type must match operation owner")
        if (
            resolved_scope.owner_id is not None
            and operation.owner_id is not None
            and resolved_scope.owner_id != operation.owner_id
        ):
            raise ValueError("browser session owner_id must match operation owner")
        if (
            resolved_scope.project_id is not None
            and operation.project_id is not None
            and resolved_scope.project_id != operation.project_id
        ):
            raise ValueError("browser session scope project must match operation project")
        return cls(
            session_id=new_id("browser_session"),
            scope=resolved_scope,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
            privacy=privacy,
            allowed_domains=allowed_domains,
        )


@dataclass(frozen=True, slots=True)
class BrowserNetworkPolicy:
    """Portable network policy inputs enforced before reference-adapter requests."""

    allowed_domains: tuple[str, ...] = ()
    denied_domains: tuple[str, ...] = ()
    allow_http: bool = True
    allow_https: bool = True
    allow_private_networks: bool = False
    max_response_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(not domain.strip() for domain in self.allowed_domains):
            raise ValueError("allowed_domains must not contain blank values")
        if any(not domain.strip() for domain in self.denied_domains):
            raise ValueError("denied_domains must not contain blank values")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
