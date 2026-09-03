import type { JsonValue } from "./types";

export interface CanonicalOwnerRef {
  type: string;
  id: string;
}

export interface AgentInstructionSource {
  content: string | null;
  ref: string | null;
  version: string | null;
}

export interface AgentRoutingRequirements {
  explicit_model_id: string | null;
  min_context_window: number | null;
  tool_calling: boolean;
  structured_output: boolean;
  streaming: boolean;
  modalities: string[];
  reasoning: string[];
  local_only: boolean;
  self_hosted_only: boolean;
}

export interface AgentCapabilityConstraint {
  capability_id: string;
  required: boolean;
  exact_version: string | null;
  minimum_version: string | null;
  maximum_version: string | null;
  required_features: string[];
  approval_ref: string | null;
}

export interface AgentProfile {
  name: string;
  role: string;
  description: string;
  enabled: boolean;
  instructions: {
    role: AgentInstructionSource;
    platform_constraint_refs: string[];
    project_instruction_refs: string[];
  };
  model: {
    requirements: AgentRoutingRequirements;
    routing_profile_ref: string | null;
    allow_task_override: boolean;
    fallback: string;
  };
  capabilities: {
    allowed: string[];
    denied: string[];
    constraints: AgentCapabilityConstraint[];
  };
  data_access: {
    memory_scopes: string[];
    memory_config_refs: string[];
    knowledge_source_ids: string[];
    allow_user_memory: boolean;
  };
  workspace_defaults: {
    project_id: string | null;
    workspace_id: string | null;
  };
  policy_hooks: {
    authorization_profile_ref: string | null;
    verification_policy_refs: string[];
  };
  resource_hints: Record<string, JsonValue>;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalAgentRevision {
  agent_id: string;
  revision: number;
  profile: AgentProfile;
  owner_ref: CanonicalOwnerRef;
  project_id: string | null;
  workspace_id: string | null;
  created_at: string;
  provenance: Record<string, JsonValue> | null;
}

export interface CanonicalAgent {
  id: string;
  type: "agent";
  current_revision: number;
  project_id: string | null;
  workspace_id: string | null;
  owner_ref: CanonicalOwnerRef;
  created_at: string;
  updated_at: string;
  revision: CanonicalAgentRevision;
}

export interface AgentRevisionRef {
  agent_id: string;
  revision: number;
}

export interface AgentTeamMember {
  agent: AgentRevisionRef;
  role: string;
  required: boolean;
  can_delegate_to: string[];
}

export interface AgentTeamProfile {
  name: string;
  members: AgentTeamMember[];
  description: string;
  coordination_policy_ref: string | null;
  leader_agent_id: string | null;
  shared_capability_ids: string[];
  max_parallel_agents: number | null;
  max_steps: number | null;
  unavailable_member_policy: string;
  enabled: boolean;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalAgentTeamRevision {
  team_id: string;
  revision: number;
  profile: AgentTeamProfile;
  owner_ref: CanonicalOwnerRef;
  project_id: string | null;
  workspace_id: string | null;
  created_at: string;
  provenance: Record<string, JsonValue> | null;
}

export interface CanonicalAgentTeam {
  id: string;
  type: "agent_team";
  current_revision: number;
  project_id: string | null;
  workspace_id: string | null;
  owner_ref: CanonicalOwnerRef;
  created_at: string;
  updated_at: string;
  revision: CanonicalAgentTeamRevision;
}

export interface AgentTeamRevisionRef {
  team_id: string;
  revision: number;
}

export type AgentRunStatus = "starting" | "running" | "succeeded" | "failed" | "cancelled";

export interface CanonicalAgentRun {
  id: string;
  type: "agent_run";
  agent_run_id: string;
  run_id: string;
  task_id: string;
  agent: AgentRevisionRef;
  status: AgentRunStatus;
  team: AgentTeamRevisionRef | null;
  selected_model_config_id: string | null;
  selected_provider_id: string | null;
  capability_ids: string[];
  orchestrator_adapter_id: string | null;
  orchestrator_runtime_ref: string | null;
  artifact_ids: string[];
  result_ids: string[];
  model_call_refs: string[];
  tool_invocation_refs: string[];
  error: string | null;
  telemetry: Record<string, JsonValue>;
  verification_context: Record<string, JsonValue>;
  started_at: string;
  finished_at: string | null;
}
