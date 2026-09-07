"""Replaceable presentation adapters for immutable canonical Skill Bundles.

Renderers may change representation only. They never alter bundle identity, add
capabilities/permissions or become authoritative Skill schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import SkillBundle
from .repository import SkillRepository


@dataclass(frozen=True, slots=True)
class RenderedSkillBundle:
    adapter_id: str
    skill_bundle_id: str
    skill_bundle_hash: str
    content: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise ValueError("Skill renderer adapter_id must not be blank")
        if not self.content.strip() and self.metadata.get("skill_count") != 0:
            raise ValueError("non-empty Skill Bundle rendering requires content")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class SkillBundleRenderer(Protocol):
    @property
    def adapter_id(self) -> str: ...

    def render(self, bundle: SkillBundle, repository: SkillRepository) -> RenderedSkillBundle: ...


class ReferenceSkillRenderer:
    """Simple deterministic plain-text renderer used by local/reference runtimes."""

    adapter_id = "reference-skill-renderer"

    def render(self, bundle: SkillBundle, repository: SkillRepository) -> RenderedSkillBundle:
        sections: list[str] = []
        for entry in bundle.entries:
            revision = repository.get_skill_revision(entry.ref.skill_id, entry.ref.revision)
            source = revision.profile.content
            body = source.content if source.content is not None else f"[content-ref: {source.ref}]"
            sections.append(
                f"SKILL {revision.skill_id}@{revision.revision} — {revision.profile.name}\n{body}"
            )
        return RenderedSkillBundle(
            adapter_id=self.adapter_id,
            skill_bundle_id=bundle.skill_bundle_id,
            skill_bundle_hash=bundle.digest,
            content="\n\n".join(sections),
            metadata={
                "skill_count": len(bundle.entries),
                "resolver_version": bundle.resolver_version,
                "policy_version": bundle.policy_version,
            },
        )


class MarkdownSkillRenderer:
    """Alternative harness representation proving canonical identity is adapter-independent."""

    adapter_id = "markdown-skill-renderer"

    def render(self, bundle: SkillBundle, repository: SkillRepository) -> RenderedSkillBundle:
        sections: list[str] = []
        for entry in bundle.entries:
            revision = repository.get_skill_revision(entry.ref.skill_id, entry.ref.revision)
            source = revision.profile.content
            body = source.content if source.content is not None else f"Content reference: `{source.ref}`"
            sections.append(
                f"## {revision.profile.name}\n\n"
                f"Canonical Skill: `{revision.skill_id}@{revision.revision}`\n\n{body}"
            )
        return RenderedSkillBundle(
            adapter_id=self.adapter_id,
            skill_bundle_id=bundle.skill_bundle_id,
            skill_bundle_hash=bundle.digest,
            content="\n\n".join(sections),
            metadata={
                "skill_count": len(bundle.entries),
                "format": "markdown",
            },
        )
