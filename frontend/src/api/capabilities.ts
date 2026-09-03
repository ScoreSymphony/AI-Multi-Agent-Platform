import type { JsonValue } from "./types";

export interface CanonicalCapabilityVersion {
  capability_id: string;
  name: string;
  version: string;
  description: string;
  input_schema: Record<string, JsonValue>;
  output_schema: Record<string, JsonValue> | null;
  tags: string[];
  safety: "standard" | "restricted" | "sensitive";
  side_effects: "none" | "local_write" | "external" | "destructive";
  required_permissions: string[];
  required_approvals: string[];
  required_worker_capabilities: string[];
  timeout_seconds: number | null;
  health: string;
  available: boolean;
  features: string[];
  credential_requirement: "none" | "required";
}

export interface CanonicalCapability {
  id: string;
  type: "capability";
  name: string;
  version_count: number;
  available: boolean;
  versions: CanonicalCapabilityVersion[];
}

export interface ProviderCapabilityDescriptor {
  name: string;
  kind: string;
  version: string;
  supported_operations: string[];
  modalities: string[];
  features: string[];
  limits: Record<string, JsonValue>;
  attributes: Record<string, JsonValue>;
}

export interface CanonicalCapabilityProvider {
  id: string;
  type: "capability-provider";
  provider_type: string;
  contract_version: string;
  supported_operations: string[];
  capabilities: ProviderCapabilityDescriptor[];
  health: string;
  available: boolean;
  limits: Record<string, JsonValue>;
  resources: Record<string, JsonValue>;
}
