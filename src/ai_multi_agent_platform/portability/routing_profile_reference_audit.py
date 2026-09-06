"""Cross-domain reference audit for routing-profile transaction compensation."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.templates.repository import TemplateRepository

from .model_routing_profile_import import RoutingProfileDependencyAudit


def build_routing_profile_dependency_audit(
    *,
    agents: AgentRepository,
    templates: TemplateRepository | None = None,
    additional_audit: RoutingProfileDependencyAudit | None = None,
) -> RoutingProfileDependencyAudit:
    """Build a complete audit over the canonical routing-profile consumers in composition.

    Agents own canonical routing-profile assignments on immutable Agent revisions. Templates
    can carry routing-profile requirements and, for Agent templates, an embedded Agent
    profile. Agent Teams only pin Agent revisions and therefore do not duplicate the routing
    profile reference.

    ``None`` means the audit could not prove reference absence. This preserves the fail-closed
    compensation contract when canonical enumeration fails, a target-shaped reference is
    malformed, or an additional domain reports an unknown result.
    """

    def audit(profile_id: str) -> tuple[str, ...] | None:
        dependencies: set[str] = set()
        try:
            for definition in agents.list_agents():
                for revision in agents.list_agent_revisions(definition.agent_id):
                    reference = revision.profile.model.routing_profile_ref
                    if reference is not None and _references_profile(reference, profile_id):
                        dependencies.add(f"agent:{revision.agent_id}@r{revision.revision}")

            if templates is not None:
                for definition in templates.list_templates():
                    for revision in templates.list_revisions(definition.template_id):
                        if _template_references_profile(revision.content, profile_id):
                            dependencies.add(
                                f"template:{revision.template_id}@r{revision.revision}"
                            )
        except (ContractError, ValueError, TypeError):
            return None

        if additional_audit is not None:
            additional = additional_audit(profile_id)
            if additional is None:
                return None
            dependencies.update(additional)

        return tuple(sorted(dependencies))

    return audit


def _template_references_profile(content: object, profile_id: str) -> bool:
    requirements = getattr(content, "requirements")
    for reference in requirements.model_policy_refs:
        if _references_profile(reference, profile_id):
            return True

    configuration = getattr(content, "configuration")
    payload = configuration.payload
    if payload is None:
        return False
    return _payload_references_profile(payload, profile_id)


def _payload_references_profile(value: FrozenJsonValue, profile_id: str) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "routing_profile_ref":
                if not isinstance(item, str):
                    raise TypeError("routing_profile_ref in Template payload must be a string")
                if _references_profile(item, profile_id):
                    return True
            if _payload_references_profile(item, profile_id):
                return True
        return False
    if isinstance(value, tuple):
        return any(_payload_references_profile(item, profile_id) for item in value)
    return False


def _references_profile(value: str, profile_id: str) -> bool:
    try:
        reference = ModelRoutingProfileRef.parse(value)
    except ValueError:
        if value.startswith(f"{profile_id}@"):
            raise
        return False
    return reference.profile_id == profile_id


__all__ = ["build_routing_profile_dependency_audit"]
