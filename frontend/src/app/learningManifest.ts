import type { APImanifest } from "../api/types";
import {
  LEARNING_DECISION_COMMANDS,
  LEARNING_OPTIONAL_RESOURCES,
  LEARNING_REQUIRED_RESOURCES,
} from "../pages/LearningPage";

export type LearningManifestState = "loading" | "available" | "read_only" | "unavailable";

export interface LearningManifestCapabilities {
  state: LearningManifestState;
  commands: string[];
  postPromotionAvailable: boolean;
}

export function learningManifestCapabilities(
  manifestState: "loading" | "ready" | "unavailable",
  manifest: APImanifest | null,
): LearningManifestCapabilities {
  if (manifestState === "loading") {
    return { state: "loading", commands: [], postPromotionAvailable: false };
  }
  if (manifestState !== "ready" || manifest === null) {
    return { state: "unavailable", commands: [], postPromotionAvailable: false };
  }
  const resources = new Set(manifest.resources);
  if (!LEARNING_REQUIRED_RESOURCES.every((resource) => resources.has(resource))) {
    return { state: "unavailable", commands: [], postPromotionAvailable: false };
  }
  const advertised = new Set(manifest.commands ?? []);
  const commands = LEARNING_DECISION_COMMANDS.filter((command) => advertised.has(command));
  return {
    state: commands.length === 0 ? "read_only" : "available",
    commands: [...commands],
    postPromotionAvailable: LEARNING_OPTIONAL_RESOURCES.every((resource) => resources.has(resource)),
  };
}
