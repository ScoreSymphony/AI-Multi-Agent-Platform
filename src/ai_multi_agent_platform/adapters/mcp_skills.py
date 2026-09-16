"""Revision-pinned MCP Skills (SEP-2640) discovery and staging PoC.

MCP is deliberately only a source/interoperability boundary here. Canonical Skill
identity, trust, review, authorization and bundle semantics remain platform-owned.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Protocol, cast
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.skills.models import (
    SkillContent,
    SkillEvaluationStatus,
    SkillProfile,
    SkillRiskLevel,
    SkillSource,
    SkillTrustStatus,
)
from ai_multi_agent_platform.skills.security_evidence import StagedSkillCandidate

MCP_SKILLS_EXTENSION_ID = "io.modelcontextprotocol/skills"
PINNED_MCP_PROTOCOL_REVISION = "2026-07-28"
PINNED_SEP_2640_REVISION = "582d814a2fb1607aa46045d0cc7f14e0f4da9945"
PINNED_EXT_SKILLS_REVISION = "d866efdba298b55b8156c7b7aa1bdebc1b625f4c"
PINNED_SEP_URL = "https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2640"
PINNED_EXT_SKILLS_URL = (
    f"https://github.com/modelcontextprotocol/ext-skills/commit/{PINNED_EXT_SKILLS_REVISION}"
)
PINNED_MAX_RESOURCES_PER_SKILL = 512
PINNED_MAX_TOTAL_SKILL_BYTES = 16_777_216


def _nonblank(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _json_value(value: object, name: str) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return cast(JsonValue, value)
    if isinstance(value, Mapping):
        mapped: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    f"{name} object keys must be strings",
                )
            mapped[key] = _json_value(item, name)
        return cast(JsonValue, mapped)
    if isinstance(value, list | tuple):
        return cast(JsonValue, [_json_value(item, name) for item in value])
    raise ContractError(
        ErrorCode.INVALID_PROVIDER_RESPONSE,
        f"{name} contains a non-JSON value",
    )


def _freeze_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        frozen = MappingProxyType(
            {str(key): _freeze_json_value(item) for key, item in value.items()}
        )
        return cast(JsonValue, frozen)
    if isinstance(value, list | tuple):
        return cast(JsonValue, tuple(_freeze_json_value(item) for item in value))
    return value


def _freeze_json_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})


def _json_mapping(value: object, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, f"{name} must be an object")
    normalized = _json_value(value, name)
    if not isinstance(normalized, dict):
        raise AssertionError("mapping normalization returned a non-mapping")
    return _freeze_json_mapping(normalized)


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"{name} must be a non-blank string",
        )
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _require_sha256(value: str, name: str) -> str:
    prefix = "sha256:"
    if not value.startswith(prefix):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"{name} must use sha256:<lowercase-hex>",
        )
    digest = value[len(prefix) :]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"{name} must use sha256:<lowercase-hex>",
        )
    return digest


def _validate_cacheable_result(payload: Mapping[str, object], method: str) -> None:
    """Validate the exact result envelope required by the pinned v1 extension shape."""

    if payload.get("resultType") != "complete":
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f'{method} response requires resultType="complete"',
        )
    ttl_ms = payload.get("ttlMs")
    if (
        not isinstance(ttl_ms, int | float)
        or isinstance(ttl_ms, bool)
        or not math.isfinite(ttl_ms)
        or ttl_ms < 0
    ):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"{method} response requires a non-negative numeric ttlMs",
        )
    cache_scope = payload.get("cacheScope")
    if cache_scope not in {"public", "private"}:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f'{method} response requires cacheScope="public" or "private"',
        )


@dataclass(frozen=True, slots=True)
class McpSkillResource:
    uri: str
    digest: str
    size: int

    def __post_init__(self) -> None:
        _nonblank(self.uri, "MCP Skill resource URI")
        _require_sha256(self.digest, "MCP Skill resource digest")
        if self.size < 0:
            raise ValueError("MCP Skill resource size must be >= 0")


@dataclass(frozen=True, slots=True)
class McpSkillEntry:
    uri: str
    frontmatter: Mapping[str, JsonValue]
    resources: tuple[McpSkillResource, ...] | str

    def __post_init__(self) -> None:
        _nonblank(self.uri, "MCP Skill URI")
        name = self.frontmatter.get("name")
        description = self.frontmatter.get("description")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("MCP Skill frontmatter requires a non-blank name")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("MCP Skill frontmatter requires a non-blank description")
        if isinstance(self.resources, str) and self.resources != "dynamic":
            raise ValueError('MCP Skill resources string must be "dynamic"')
        if not isinstance(self.resources, str):
            uris = [item.uri for item in self.resources]
            if len(set(uris)) != len(uris):
                raise ValueError("MCP Skill resource URIs must be unique")
            if self.uri not in set(uris):
                raise ValueError("MCP Skill manifest must include its SKILL.md URI")
        object.__setattr__(self, "frontmatter", _freeze_json_mapping(self.frontmatter))


@dataclass(frozen=True, slots=True)
class McpSkillListPage:
    skills: tuple[McpSkillEntry, ...]
    next_cursor: str | None = None


class McpSkillsCompatibilityStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class McpSkillsCompatibility:
    status: McpSkillsCompatibilityStatus
    reason: str
    directory_read: bool = False


@dataclass(frozen=True, slots=True)
class McpSkillsLimits:
    max_files: int = PINNED_MAX_RESOURCES_PER_SKILL
    max_file_bytes: int = PINNED_MAX_TOTAL_SKILL_BYTES
    max_total_bytes: int = PINNED_MAX_TOTAL_SKILL_BYTES
    max_list_pages: int = 100

    def __post_init__(self) -> None:
        for value, name in (
            (self.max_files, "max_files"),
            (self.max_file_bytes, "max_file_bytes"),
            (self.max_total_bytes, "max_total_bytes"),
            (self.max_list_pages, "max_list_pages"),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be > 0")


class McpSkillsRpc(Protocol):
    """Minimal transport seam; SDK/provider details stay outside the Skill model."""

    @property
    def server_identity(self) -> str: ...

    @property
    def protocol_revision(self) -> str: ...

    @property
    def server_capabilities(self) -> Mapping[str, object]: ...

    def call(self, method: str, params: Mapping[str, JsonValue]) -> Mapping[str, object]: ...

    def read_resource(self, uri: str, *, max_bytes: int) -> bytes:
        """Read at most ``max_bytes`` and abort before materializing an oversized body."""
        ...


@dataclass(frozen=True, slots=True)
class McpSkillCanonicalCandidate:
    staged: StagedSkillCandidate
    profile: SkillProfile
    provenance: Provenance
    remote_entry: McpSkillEntry
    server_identity: str
    manifest_digest: str
    fetched_at: datetime


def parse_skill_entry(payload: object) -> McpSkillEntry:
    if not isinstance(payload, Mapping):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE, "MCP Skill entry must be an object"
        )
    uri = _required_string(payload.get("uri"), "MCP Skill uri")
    frontmatter = _json_mapping(payload.get("frontmatter"), "MCP Skill frontmatter")
    raw_resources = payload.get("resources")
    if raw_resources == "dynamic":
        resources: tuple[McpSkillResource, ...] | str = "dynamic"
    elif isinstance(raw_resources, list):
        parsed: list[McpSkillResource] = []
        for raw in raw_resources:
            if not isinstance(raw, Mapping):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "MCP Skill resource entry must be an object",
                )
            raw_size = raw.get("size")
            if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "MCP Skill resource size must be a non-negative integer",
                )
            parsed.append(
                McpSkillResource(
                    uri=_required_string(raw.get("uri"), "MCP Skill resource uri"),
                    digest=_required_string(raw.get("digest"), "MCP Skill resource digest"),
                    size=raw_size,
                )
            )
        resources = tuple(parsed)
    else:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            'MCP Skill resources must be a complete array or "dynamic"',
        )
    try:
        entry = McpSkillEntry(uri=uri, frontmatter=frontmatter, resources=resources)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, str(exc)) from exc
    name = cast(str, entry.frontmatter["name"])
    if _skill_name_from_uri(entry.uri) != name:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill URI path must end in frontmatter.name/SKILL.md",
        )
    return entry


def parse_list_page(payload: Mapping[str, object]) -> McpSkillListPage:
    _validate_cacheable_result(payload, "skills/list")
    raw_skills = payload.get("skills")
    if not isinstance(raw_skills, list):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "skills/list response requires a skills array",
        )
    cursor = _optional_string(payload.get("nextCursor"), "skills/list nextCursor")
    return McpSkillListPage(
        skills=tuple(parse_skill_entry(item) for item in raw_skills),
        next_cursor=cursor,
    )


class McpSkillsAdapter:
    """Fail-closed, revision-pinned SEP-2640 discovery/fetch staging adapter."""

    def __init__(
        self,
        rpc: McpSkillsRpc,
        staging_root: Path,
        *,
        limits: McpSkillsLimits | None = None,
    ) -> None:
        self.rpc = rpc
        self.staging_root = staging_root
        self.limits = limits or McpSkillsLimits()

    def detect(self) -> McpSkillsCompatibility:
        if self.rpc.protocol_revision != PINNED_MCP_PROTOCOL_REVISION:
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.INCOMPATIBLE,
                "protocol_revision_mismatch",
            )
        capabilities = self.rpc.server_capabilities
        resources = capabilities.get("resources")
        if not isinstance(resources, Mapping):
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.INCOMPATIBLE,
                "resources_capability_missing_or_malformed",
            )
        extensions = capabilities.get("extensions")
        if extensions is None:
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.UNSUPPORTED,
                "skills_extension_not_declared",
            )
        if not isinstance(extensions, Mapping):
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.INCOMPATIBLE,
                "extensions_capability_malformed",
            )
        extension = extensions.get(MCP_SKILLS_EXTENSION_ID)
        if extension is None:
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.UNSUPPORTED,
                "skills_extension_not_declared",
            )
        if not isinstance(extension, Mapping):
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.INCOMPATIBLE,
                "skills_extension_settings_malformed",
            )
        unknown = set(extension) - {"directoryRead"}
        if unknown:
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.INCOMPATIBLE,
                "unknown_skills_extension_settings",
            )
        directory_read = extension.get("directoryRead", False)
        if not isinstance(directory_read, bool):
            return McpSkillsCompatibility(
                McpSkillsCompatibilityStatus.INCOMPATIBLE,
                "directory_read_setting_malformed",
            )
        return McpSkillsCompatibility(
            McpSkillsCompatibilityStatus.SUPPORTED,
            "pinned_revision_supported",
            directory_read=directory_read,
        )

    def discover(self) -> tuple[McpSkillEntry, ...]:
        self._require_supported()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        entries: dict[str, McpSkillEntry] = {}
        for _ in range(self.limits.max_list_pages):
            params: dict[str, JsonValue] = {}
            if cursor is not None:
                params["cursor"] = cursor
            page = parse_list_page(self.rpc.call("skills/list", params))
            for entry in page.skills:
                if entry.uri in entries:
                    raise ContractError(
                        ErrorCode.INVALID_PROVIDER_RESPONSE,
                        f"duplicate MCP Skill URI in listing: {entry.uri}",
                    )
                entries[entry.uri] = entry
            if page.next_cursor is None:
                return tuple(entries.values())
            if page.next_cursor in seen_cursors:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "skills/list cursor loop detected",
                )
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        raise ContractError(
            ErrorCode.RESOURCE_EXHAUSTED,
            "skills/list exceeded the configured page limit",
        )

    def get(self, uri: str) -> McpSkillEntry:
        self._require_supported()
        _nonblank(uri, "MCP Skill URI")
        payload = self.rpc.call("skills/get", {"uri": uri})
        _validate_cacheable_result(payload, "skills/get")
        raw_skill = payload.get("skill")
        entry = parse_skill_entry(raw_skill)
        if entry.uri != uri:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "skills/get returned a different Skill URI",
            )
        return entry

    def fetch_and_stage(self, uri: str) -> McpSkillCanonicalCandidate:
        entry = self.get(uri)
        if isinstance(entry.resources, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "dynamic MCP Skill content is not reproducible and cannot enter canonical intake",
                provider_id=self.rpc.server_identity,
            )
        resources = entry.resources
        if len(resources) > self.limits.max_files:
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "MCP Skill exceeds the configured file-count limit",
            )
        total_size = sum(item.size for item in resources)
        if any(item.size > self.limits.max_file_bytes for item in resources):
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "MCP Skill contains a file above the configured size limit",
            )
        if total_size > self.limits.max_total_bytes:
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "MCP Skill exceeds the configured total-size limit",
            )

        mapped: list[tuple[PurePosixPath, McpSkillResource]] = []
        casefolded: set[str] = set()
        for resource in resources:
            relative = _safe_relative_resource_path(entry.uri, resource.uri)
            folded = relative.as_posix().casefold()
            if folded in casefolded:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "MCP Skill manifest contains a path collision",
                )
            casefolded.add(folded)
            mapped.append((relative, resource))

        self.staging_root.mkdir(parents=True, exist_ok=True)
        temp_path = Path(tempfile.mkdtemp(prefix=".mcp-skill-", dir=self.staging_root))
        try:
            for relative, resource in sorted(mapped, key=lambda item: item[0].as_posix()):
                content = self.rpc.read_resource(resource.uri, max_bytes=resource.size)
                if not isinstance(content, bytes):
                    raise ContractError(
                        ErrorCode.INVALID_PROVIDER_RESPONSE,
                        "resources/read normalization must return raw bytes",
                    )
                if len(content) > resource.size:
                    raise ContractError(
                        ErrorCode.RESOURCE_EXHAUSTED,
                        f"MCP Skill resource exceeded bounded read: {resource.uri}",
                    )
                if len(content) != resource.size:
                    raise ContractError(
                        ErrorCode.INVALID_PROVIDER_RESPONSE,
                        f"MCP Skill resource size mismatch: {resource.uri}",
                    )
                actual = sha256(content).hexdigest()
                expected = _require_sha256(resource.digest, "MCP Skill resource digest")
                if actual != expected:
                    raise ContractError(
                        ErrorCode.INVALID_PROVIDER_RESPONSE,
                        f"MCP Skill resource digest mismatch: {resource.uri}",
                    )
                target = temp_path.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            snapshot_digest = _digest_tree(temp_path)
            manifest_digest = _manifest_digest(entry, mapped)
            candidate_id = _candidate_id(
                self.rpc.server_identity,
                entry.uri,
                snapshot_digest,
            )
            target_path = self.staging_root / snapshot_digest
            if target_path.exists():
                _make_read_only(target_path)
                if _digest_tree(target_path) != snapshot_digest:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "existing staged MCP Skill snapshot failed digest validation",
                    )
                shutil.rmtree(temp_path)
            else:
                _make_read_only(temp_path)
                os.replace(temp_path, target_path)
                _make_read_only(target_path)
            staged = StagedSkillCandidate(
                candidate_id=candidate_id,
                candidate_revision=1,
                candidate_digest=snapshot_digest,
                snapshot_path=target_path,
            )
            fetched_at = datetime.now(UTC)
            profile, provenance = self._canonical_mapping(
                entry,
                staged,
                manifest_digest,
                fetched_at,
            )
            return McpSkillCanonicalCandidate(
                staged=staged,
                profile=profile,
                provenance=provenance,
                remote_entry=entry,
                server_identity=self.rpc.server_identity,
                manifest_digest=manifest_digest,
                fetched_at=fetched_at,
            )
        # error-boundary: allow-broad-catch=cleanup local rollback/settlement re-raises primary failure
        except Exception:
            if temp_path.exists():
                try:
                    _make_writable(temp_path)
                except OSError:
                    pass
                shutil.rmtree(temp_path, ignore_errors=True)
            raise

    def _canonical_mapping(
        self,
        entry: McpSkillEntry,
        staged: StagedSkillCandidate,
        manifest_digest: str,
        fetched_at: datetime,
    ) -> tuple[SkillProfile, Provenance]:
        frontmatter = dict(entry.frontmatter)
        raw_license = frontmatter.get("license")
        license_name = (
            raw_license if isinstance(raw_license, str) and raw_license.strip() else "UNKNOWN"
        )
        declarations: dict[str, JsonValue] = {
            key: frontmatter[key]
            for key in ("allowed-tools", "allowed_tools", "tools", "capabilities")
            if key in frontmatter
        }
        source_revision = (
            f"sep2640:{PINNED_SEP_2640_REVISION};"
            f"ext:{PINNED_EXT_SKILLS_REVISION};manifest:{manifest_digest}"
        )
        source = SkillSource(
            source_url=f"mcp-skills:{self.rpc.server_identity}:{entry.uri}",
            source_revision=source_revision,
            license=license_name,
            checksum=staged.candidate_digest,
            requested_capability_ids=(),
        )
        metadata: dict[str, JsonValue] = {
            "origin": "mcp_skills",
            "mcp_server_identity": self.rpc.server_identity,
            "mcp_skill_uri": entry.uri,
            "mcp_protocol_revision": PINNED_MCP_PROTOCOL_REVISION,
            "sep_2640_revision": PINNED_SEP_2640_REVISION,
            "ext_skills_revision": PINNED_EXT_SKILLS_REVISION,
            "manifest_digest": manifest_digest,
            "snapshot_digest": staged.candidate_digest,
            "fetched_at": fetched_at.isoformat(),
            "frontmatter_evidence": frontmatter,
            "remote_declarations_are_review_inputs_only": True,
            "remote_capability_declarations": declarations,
            "manifest_completeness": "server_asserted_complete",
        }
        profile = SkillProfile(
            name=cast(str, frontmatter["name"]),
            description=cast(str, frontmatter["description"]),
            purpose_categories=("external", "mcp"),
            content=SkillContent(
                ref=f"skill-snapshot://sha256/{staged.candidate_digest}",
                version=manifest_digest,
            ),
            capability_requirements=(),
            risk_level=SkillRiskLevel.HIGH,
            trust_status=SkillTrustStatus.DISCOVERED,
            evaluation_status=SkillEvaluationStatus.NOT_EVALUATED,
            source=source,
            enabled=False,
            metadata=metadata,
        )
        provenance = Provenance(
            source="mcp_skills_sep_2640",
            details={
                "server_identity": self.rpc.server_identity,
                "remote_skill_uri": entry.uri,
                "mcp_protocol_revision": PINNED_MCP_PROTOCOL_REVISION,
                "sep_2640_revision": PINNED_SEP_2640_REVISION,
                "sep_2640_url": PINNED_SEP_URL,
                "ext_skills_revision": PINNED_EXT_SKILLS_REVISION,
                "ext_skills_url": PINNED_EXT_SKILLS_URL,
                "manifest_digest": manifest_digest,
                "snapshot_digest": staged.candidate_digest,
                "fetch_timestamp": fetched_at.isoformat(),
                "server_frontmatter": frontmatter,
                "declared_capabilities_are_not_grants": True,
            },
        )
        return profile, provenance

    def _require_supported(self) -> McpSkillsCompatibility:
        compatibility = self.detect()
        if compatibility.status is McpSkillsCompatibilityStatus.SUPPORTED:
            return compatibility
        code = (
            ErrorCode.UNSUPPORTED_CAPABILITY
            if compatibility.status is McpSkillsCompatibilityStatus.UNSUPPORTED
            else ErrorCode.INVALID_PROVIDER_RESPONSE
        )
        raise ContractError(
            code,
            f"MCP Skills unavailable: {compatibility.reason}",
            provider_id=self.rpc.server_identity,
            details={
                "expected_protocol_revision": PINNED_MCP_PROTOCOL_REVISION,
                "actual_protocol_revision": self.rpc.protocol_revision,
                "reason": compatibility.reason,
            },
        )


def _candidate_id(server_identity: str, uri: str, snapshot_digest: str) -> str:
    identity = f"mcp-skills:{server_identity}:{uri}:sha256:{snapshot_digest}"
    return f"skill_{uuid5(NAMESPACE_URL, identity)}"


def _manifest_digest(
    entry: McpSkillEntry,
    mapped: list[tuple[PurePosixPath, McpSkillResource]],
) -> str:
    payload = {
        "skill_uri": entry.uri,
        "resources": [
            {
                "path": relative.as_posix(),
                "uri": resource.uri,
                "digest": resource.digest,
                "size": resource.size,
            }
            for relative, resource in sorted(mapped, key=lambda item: item[0].as_posix())
        ],
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _skill_name_from_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if not parsed.scheme:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill URI must be absolute",
        )
    segments = _decoded_segments(parsed.path, "MCP Skill URI")
    if not segments or segments[-1] != "SKILL.md":
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill URI must end in SKILL.md",
        )
    skill_path = segments[:-1]
    if skill_path:
        return skill_path[-1]
    authority = unquote(parsed.netloc)
    if (
        not authority
        or authority in {".", ".."}
        or "/" in authority
        or "\\" in authority
        or "\x00" in authority
        or not authority.strip()
    ):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill URI must contain a valid skill path",
        )
    return authority


def _safe_relative_resource_path(skill_uri: str, resource_uri: str) -> PurePosixPath:
    skill = urlsplit(skill_uri)
    resource = urlsplit(resource_uri)
    if not skill.scheme or not resource.scheme:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill resource URIs must be absolute",
        )
    if (skill.scheme, skill.netloc, skill.query, skill.fragment) != (
        resource.scheme,
        resource.netloc,
        resource.query,
        resource.fragment,
    ):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill resource is outside the Skill URI origin",
        )
    skill_segments = _decoded_segments(skill.path, "MCP Skill URI")
    resource_segments = _decoded_segments(resource.path, "MCP Skill resource URI")
    if not skill_segments or skill_segments[-1] != "SKILL.md":
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill URI must end in SKILL.md",
        )
    root = skill_segments[:-1]
    if resource_segments[: len(root)] != root or len(resource_segments) <= len(root):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP Skill resource is outside the Skill directory",
        )
    relative = resource_segments[len(root) :]
    return PurePosixPath(*relative)


def _decoded_segments(path: str, name: str) -> tuple[str, ...]:
    segments: list[str] = []
    for raw in path.split("/"):
        if not raw:
            continue
        decoded = unquote(raw)
        if (
            decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or "\x00" in decoded
            or not decoded.strip()
        ):
            raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, f"unsafe {name} path")
        segments.append(decoded)
    return tuple(segments)


def _update_digest_field(digest: object, payload: bytes) -> None:
    """NUL-escape a field before its delimiter so the framing is unambiguous."""

    digest.update(payload.replace(b"\0", b"\0\0"))  # type: ignore[attr-defined]
    digest.update(b"\0")  # type: ignore[attr-defined]


def _digest_tree(root: Path) -> str:
    digest = sha256()
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        _update_digest_field(digest, path.relative_to(root).as_posix().encode())
        _update_digest_field(digest, path.read_bytes())
    return digest.hexdigest()


def _make_read_only(root: Path) -> None:
    if os.name != "posix":
        return
    for path in sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_writable(root: Path) -> None:
    if os.name != "posix":
        return
    root.chmod(0o755)
    for path in sorted(root.rglob("*"), key=lambda value: len(value.parts)):
        path.chmod(0o755 if path.is_dir() else 0o644)
