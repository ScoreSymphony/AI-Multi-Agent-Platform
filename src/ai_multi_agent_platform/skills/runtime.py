"""Runtime binding between immutable Skill Bundle evidence and canonical AgentRun records."""

from __future__ import annotations

from ai_multi_agent_platform.agents import AgentRunRecord
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .adapters import RenderedSkillBundle, SkillBundleRenderer
from .models import SkillBundle, SkillRunBinding
from .repository import SkillRepository
from .resolver import SkillResolutionRequest, SkillResolver


class SkillExecutionCoordinator:
    """Materialize Skill evidence and pin it to the exact canonical AgentRun.

    The coordinator does not execute Skills. It owns the method-resolution evidence
    boundary and therefore cannot grant capabilities, approvals or verification state.
    """

    def __init__(self, resolver: SkillResolver) -> None:
        self.resolver = resolver
        self.repository: SkillRepository = resolver.repository

    def prepare(
        self,
        request: SkillResolutionRequest,
    ) -> tuple[SkillBundle, SkillRunBinding]:
        return self.resolver.resolve_and_bind(request)

    def bind_agent_run(self, binding_id: str, record: AgentRunRecord) -> SkillRunBinding:
        binding = self.repository.get_binding(binding_id)
        if (
            binding.run_id != record.run_id
            or binding.task_id != record.task_id
            or binding.agent_id != record.agent.agent_id
            or binding.agent_revision != record.agent.revision
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "AgentRun execution identity does not match prepared Skill Bundle evidence",
                details={
                    "binding_id": binding_id,
                    "agent_run_id": record.agent_run_id,
                },
            )
        return self.repository.bind_agent_run(binding_id, record.agent_run_id)

    def bundle_for_binding(self, binding_id: str) -> SkillBundle:
        binding = self.repository.get_binding(binding_id)
        bundle = self.repository.get_bundle(binding.skill_bundle_id)
        if bundle.digest != binding.skill_bundle_hash:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored Skill Bundle no longer matches historical binding evidence",
            )
        return bundle

    def render(
        self,
        skill_bundle_id: str,
        renderer: SkillBundleRenderer,
    ) -> RenderedSkillBundle:
        bundle = self.repository.get_bundle(skill_bundle_id)
        rendered = renderer.render(bundle, self.repository)
        if (
            rendered.skill_bundle_id != bundle.skill_bundle_id
            or rendered.skill_bundle_hash != bundle.digest
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Skill renderer attempted to alter canonical bundle identity",
                details={"adapter_id": renderer.adapter_id},
            )
        return rendered
