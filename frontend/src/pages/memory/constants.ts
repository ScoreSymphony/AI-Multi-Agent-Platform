import type {
  MemoryOrigin,
  MemoryRetention,
  MemoryScope,
  MemoryType,
} from "../../api/memoryKnowledge";

export const MEMORY_SCOPES: MemoryScope[] = [
  "user",
  "workspace",
  "agent",
  "task",
  "organization",
  "historical",
  "short_term",
];

export const DURABLE_MEMORY_SCOPES: Exclude<MemoryScope, "short_term">[] = [
  "user",
  "workspace",
  "agent",
  "task",
  "organization",
  "historical",
];

export const MEMORY_ORIGINS: MemoryOrigin[] = ["user-authored", "agent-derived", "imported"];

export const SEMANTIC_MEMORY_TYPES: Exclude<MemoryType, "unclassified">[] = [
  "episodic",
  "semantic",
  "procedural",
  "preference",
  "reflective",
];

export const MEMORY_TYPES: MemoryType[] = [...SEMANTIC_MEMORY_TYPES, "unclassified"];

export const MEMORY_RETENTIONS: MemoryRetention[] = [
  "ephemeral",
  "task_lifetime",
  "project_lifetime",
  "user_lifetime",
  "durable",
  "until",
];
