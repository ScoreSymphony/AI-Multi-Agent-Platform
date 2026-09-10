import type { RegistryItem } from "../api/registry";

export const TECHNICAL_CATEGORIES = [
  ["code-intelligence", "Code intelligence"],
  ["coding-agent", "Coding agents"],
  ["agent-framework", "Agent frameworks"],
  ["specification-and-skills", "Specification & skills"],
  ["memory-and-context", "Memory & context"],
  ["evaluation", "Evaluation"],
  ["security", "Security"],
  ["browser-and-execution", "Browser & execution"],
  ["inference-runtime", "Inference runtimes"],
  ["retrieval", "Retrieval"],
  ["model-and-dataset-tooling", "Model & dataset tooling"],
  ["music-ai", "Music AI"],
] as const;

const TECHNICAL_CATEGORY_IDS = new Set(TECHNICAL_CATEGORIES.map(([id]) => id));

export type TechnicalLifecycle =
  | "discovered"
  | "candidate"
  | "pilot"
  | "adopted"
  | "reference"
  | "deferred"
  | "rejected"
  | "deprecated"
  | "unknown";

export interface TechnicalMarketplaceMetadata {
  categories: string[];
  lifecycle: TechnicalLifecycle;
  evaluation: string;
  deploymentModes: string[];
  costStatus: string;
  networkStatus: string;
  providerRequirements: string[];
  alternatives: string[];
  resourceClass: string | null;
  architectureReference: string | null;
  decisionReference: string | null;
  evaluationReference: string | null;
}

export function technicalMetadata(item: RegistryItem): TechnicalMarketplaceMetadata | null {
  const categories = item.categories.filter((category) => TECHNICAL_CATEGORY_IDS.has(category as never));
  if (categories.length === 0) return null;
  return {
    categories,
    lifecycle: singleTag(item.tags, "lifecycle:", "unknown") as TechnicalLifecycle,
    evaluation: singleTag(item.tags, "evaluation:", "unknown"),
    deploymentModes: multiTags(item.tags, "deployment:", ["unknown"]),
    costStatus: singleTag(item.tags, "cost:", "unknown"),
    networkStatus: singleTag(item.tags, "network:", "unknown"),
    providerRequirements: multiTags(item.tags, "provider:", []),
    alternatives: multiTags(item.tags, "alternative:", []),
    resourceClass: optionalTag(item.tags, "resource:"),
    architectureReference: optionalTag(item.tags, "architecture-ref:"),
    decisionReference: optionalTag(item.tags, "decision-ref:"),
    evaluationReference: optionalTag(item.tags, "evaluation-ref:"),
  };
}

export function technicalCategoryLabel(category: string): string {
  return TECHNICAL_CATEGORIES.find(([id]) => id === category)?.[1] ?? category;
}

function singleTag(tags: string[], prefix: string, fallback: string): string {
  return multiTags(tags, prefix, [fallback])[0] ?? fallback;
}

function optionalTag(tags: string[], prefix: string): string | null {
  const values = multiTags(tags, prefix, []);
  return values.length === 1 ? values[0] : null;
}

function multiTags(tags: string[], prefix: string, fallback: string[]): string[] {
  const values = tags
    .filter((tag) => tag.startsWith(prefix))
    .map((tag) => tag.slice(prefix.length))
    .filter(Boolean)
    .sort();
  return values.length > 0 ? values : fallback;
}
