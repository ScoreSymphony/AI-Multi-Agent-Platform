"""Canonical Agent/Team mapping for the optional Hermes adapter."""

from __future__ import annotations

from ai_multi_agent_platform.agents.models import AgentExecutionSpec, OrchestratorMapping
from ai_multi_agent_platform.agents.runtime import AgentOrchestratorMapper
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .hermes_config import HERMES_ADAPTER_ID, HermesAdapterConfig


class HermesAgentMapper(AgentOrchestratorMapper):
    """Map exact canonical Agent/Team revisions into Hermes-private runtime metadata."""

    adapter_id = HERMES_ADAPTER_ID

    def __init__(self, config: HermesAdapterConfig) -> None:
        self.config = config

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping:
        if not self.config.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Hermes orchestrator adapter is disabled",
                provider_id=HERMES_ADAPTER_ID,
            )
        model = self._model_mapping(spec)
        capabilities = self._capability_mappings(spec)
        agent = spec.agent_revision
        role_source = agent.profile.instructions.role
        profile: dict[str, JsonValue] = {
            "canonical_agent_id": agent.agent_id,
            "canonical_revision": agent.revision,
            "name": agent.profile.name,
            "role": agent.profile.role,
            "description": agent.profile.description,
            "instructions": {
                "role_content": role_source.content,
                "role_ref": role_source.ref,
                "role_version": role_source.version,
                "platform_constraint_refs": list(
                    agent.profile.instructions.platform_constraint_refs
                ),
                "project_instruction_refs": list(
                    agent.profile.instructions.project_instruction_refs
                ),
            },
            "model": model,
            "capabilities": capabilities,
            "data_access": {
                "memory_scopes": [scope.value for scope in agent.profile.data_access.memory_scopes],
                "memory_config_refs": list(agent.profile.data_access.memory_config_refs),
                "knowledge_source_ids": list(agent.profile.data_access.knowledge_source_ids),
                "allow_user_memory": agent.profile.data_access.allow_user_memory,
            },
            "policy": {
                "authorization_profile_ref": agent.profile.policy_hooks.authorization_profile_ref,
                "verification_policy_refs": list(
                    agent.profile.policy_hooks.verification_policy_refs
                ),
            },
            "task_context": dict(spec.task_context),
            "project_context": dict(spec.project_context),
        }
        metadata: dict[str, JsonValue] = {
            "mapping_kind": "hermes.api-server.agent/v1",
            "upstream_revision": self.config.pinned_revision,
            "compatibility_status": self.config.compatibility_status.value,
            "agent": profile,
        }
        if spec.team_revision is not None:
            team = spec.team_revision
            metadata["team"] = {
                "canonical_team_id": team.team_id,
                "canonical_revision": team.revision,
                "name": team.profile.name,
                "coordination_policy_ref": team.profile.coordination_policy_ref,
                "leader_agent_id": team.profile.leader_agent_id,
                "members": [
                    {
                        "agent_id": member.agent.agent_id,
                        "revision": member.agent.revision,
                        "role": member.role,
                        "required": member.required,
                        "can_delegate_to": list(member.can_delegate_to),
                    }
                    for member in team.profile.members
                ],
                "shared_capability_ids": list(team.profile.shared_capability_ids),
                "shared_resource_refs": list(team.profile.shared_resource_refs),
                "max_parallel_agents": team.profile.max_parallel_agents,
                "max_steps": team.profile.max_steps,
                "unavailable_member_policy": team.profile.unavailable_member_policy.value,
            }
        runtime_ref = f"hermes:mapping:{agent.agent_id}:r{agent.revision}:{spec.run_id}"
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=runtime_ref,
            metadata=metadata,
        )

    def _model_mapping(self, spec: AgentExecutionSpec) -> dict[str, JsonValue]:
        canonical_id = spec.selected_model_config_id
        if canonical_id is None:
            return {
                "canonical_model_config_id": None,
                "provider_id": spec.selected_provider_id,
                "hermes_model": None,
            }
        hermes_model = self.config.model_bridge.get(canonical_id)
        if hermes_model is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Hermes model bridge has no target for the selected canonical model",
                provider_id=HERMES_ADAPTER_ID,
                details={"model_config_id": canonical_id},
            )
        return {
            "canonical_model_config_id": canonical_id,
            "provider_id": spec.selected_provider_id,
            "hermes_model": hermes_model,
        }

    def _capability_mappings(
        self,
        spec: AgentExecutionSpec,
    ) -> list[JsonValue]:
        result: list[JsonValue] = []
        for capability_id in spec.capability_ids:
            hermes_tool = self.config.capability_bridge.get(capability_id)
            if hermes_tool is None:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Hermes capability bridge has no target for a canonical capability",
                    provider_id=HERMES_ADAPTER_ID,
                    details={"capability_id": capability_id},
                )
            result.append(
                {
                    "canonical_capability_id": capability_id,
                    "canonical_version": spec.capability_versions.get(capability_id),
                    "hermes_tool": hermes_tool,
                }
            )
        return result
