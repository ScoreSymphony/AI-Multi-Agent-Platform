import type { MemoryScope, MemoryType } from "../../api/memoryKnowledge";

export type MemoryTypeFilter = MemoryType | "all";

export interface MemoryQueryIdentity {
  scope: MemoryScope;
  scopeId: string;
  projectId: string;
  search: string;
  memoryType: MemoryTypeFilter;
  includeExpired: boolean;
  includeSuperseded: boolean;
}

export function memoryTypeFilterValue(value: MemoryTypeFilter): MemoryType | undefined {
  return value === "all" ? undefined : value;
}

export function buildMemoryQueryKey(input: MemoryQueryIdentity): string {
  return [
    input.scope,
    input.scopeId.trim(),
    input.projectId.trim(),
    input.search.trim(),
    input.memoryType,
    input.includeExpired,
    input.includeSuperseded,
  ].join("|");
}
