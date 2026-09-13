import type {
  AgentInstructionSource,
  AgentProfile,
  AgentTeamMember,
  AgentTeamProfile,
} from "../../api/agents";

export type CapabilityBucket = "allowed" | "required" | "denied";

export function emptyAgentProfile(): AgentProfile {
  return {
    name: "",
    role: "",
    description: "",
    enabled: true,
    instructions: {
      role: { content: "Describe this Agent's responsibilities.", ref: null, version: null },
      platform_constraint_refs: [],
      project_instruction_refs: [],
    },
    model: {
      requirements: {
        explicit_model_id: null,
        min_context_window: null,
        tool_calling: false,
        structured_output: false,
        streaming: false,
        modalities: [],
        reasoning: [],
        local_only: false,
        self_hosted_only: false,
      },
      routing_profile_ref: null,
      allow_task_override: false,
      fallback: "fail",
    },
    capabilities: { allowed: [], denied: [], constraints: [] },
    data_access: {
      memory_scopes: [],
      memory_config_refs: [],
      knowledge_source_ids: [],
      allow_user_memory: false,
    },
    workspace_defaults: { project_id: null, workspace_id: null },
    policy_hooks: { authorization_profile_ref: null, verification_policy_refs: [] },
    resource_hints: {},
    metadata: {},
  };
}

export function emptyTeamProfile(): AgentTeamProfile {
  return {
    name: "",
    members: [],
    description: "",
    coordination_policy_ref: null,
    leader_agent_id: null,
    shared_capability_ids: [],
    shared_resource_refs: [],
    max_parallel_agents: null,
    max_steps: null,
    unavailable_member_policy: "fail",
    enabled: true,
    metadata: {},
  };
}

export function normalizeAgentProfile(
  profile: AgentProfile,
  projectId: string | null,
  workspaceId: string | null,
): AgentProfile {
  return {
    ...profile,
    name: profile.name.trim(),
    role: profile.role.trim(),
    description: profile.description.trim(),
    workspace_defaults: { project_id: projectId, workspace_id: workspaceId },
    instructions: {
      ...profile.instructions,
      role: normalizeInstruction(profile.instructions.role),
    },
  };
}

function normalizeInstruction(source: AgentInstructionSource): AgentInstructionSource {
  if (source.ref !== null) {
    return { content: null, ref: source.ref.trim(), version: source.version?.trim() || null };
  }
  return { content: source.content?.trim() || null, ref: null, version: source.version?.trim() || null };
}

export function validateAgent(profile: AgentProfile): string | null {
  if (!profile.name.trim()) return "Agent name is required.";
  if (!profile.role.trim()) return "Agent role is required.";
  if (profile.instructions.role.ref !== null && !profile.instructions.role.ref.trim()) {
    return "Instruction reference cannot be blank.";
  }
  if (profile.instructions.role.ref === null && !profile.instructions.role.content?.trim()) {
    return "Inline instruction content is required.";
  }
  if (profile.data_access.memory_scopes.includes("user") && !profile.data_access.allow_user_memory) {
    return "User memory scope requires Allow user memory.";
  }
  return null;
}

export function validateTeam(profile: AgentTeamProfile): string | null {
  if (!profile.name.trim()) return "Team name is required.";
  if (!profile.members.length) return "A Team requires at least one pinned Agent member.";
  const memberIds = new Set(profile.members.map((member) => member.agent.agent_id));
  if (profile.leader_agent_id && !memberIds.has(profile.leader_agent_id)) {
    return "Team leader must be one of the pinned members.";
  }
  for (const member of profile.members) {
    if (!member.role.trim()) return "Every Team member requires a non-blank role.";
    if (member.can_delegate_to.some((targetId) => targetId === member.agent.agent_id || !memberIds.has(targetId))) {
      return "Delegation targets must be other pinned Team members.";
    }
  }
  return null;
}

export function withInstructionChanges(
  profile: AgentProfile,
  changes: Partial<AgentInstructionSource>,
): AgentProfile {
  return {
    ...profile,
    instructions: {
      ...profile.instructions,
      role: { ...profile.instructions.role, ...changes },
    },
  };
}

export function withMemberChanges(
  profile: AgentTeamProfile,
  agentId: string,
  changes: Partial<AgentTeamMember>,
): AgentTeamProfile {
  return {
    ...profile,
    members: profile.members.map((member) =>
      member.agent.agent_id === agentId ? { ...member, ...changes } : member,
    ),
  };
}

export function updateCapabilityBucket(
  profile: AgentProfile,
  bucket: CapabilityBucket,
  values: string[],
): AgentProfile {
  const existingRequired = profile.capabilities.constraints.filter((item) => item.required);
  if (bucket === "required") {
    const nextRequired = values.map((capabilityId) =>
      existingRequired.find((item) => item.capability_id === capabilityId) ?? {
        capability_id: capabilityId,
        required: true,
        exact_version: null,
        minimum_version: null,
        maximum_version: null,
        required_features: [],
        approval_ref: null,
      },
    );
    return {
      ...profile,
      capabilities: {
        ...profile.capabilities,
        allowed: [...new Set([
          ...profile.capabilities.allowed.filter((id) => !profile.capabilities.denied.includes(id)),
          ...values,
        ])],
        denied: profile.capabilities.denied.filter((id) => !values.includes(id)),
        constraints: [
          ...profile.capabilities.constraints.filter((item) => !item.required),
          ...nextRequired,
        ],
      },
    };
  }
  if (bucket === "denied") {
    return {
      ...profile,
      capabilities: {
        ...profile.capabilities,
        denied: values,
        allowed: profile.capabilities.allowed.filter((id) => !values.includes(id)),
        constraints: profile.capabilities.constraints.filter((item) => !values.includes(item.capability_id)),
      },
    };
  }
  const requiredIds = new Set(existingRequired.map((item) => item.capability_id));
  return {
    ...profile,
    capabilities: {
      ...profile.capabilities,
      allowed: [...new Set([...values, ...requiredIds])],
      denied: profile.capabilities.denied.filter((id) => !values.includes(id)),
    },
  };
}
