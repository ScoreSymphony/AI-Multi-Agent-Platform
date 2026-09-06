import type { APImanifest } from "../api/types";

export type TemplateManifestState = "loading" | "available" | "unavailable";
export type ControlPlaneManifestState = "loading" | "ready" | "unavailable";

export const TEMPLATE_REQUIRED_RESOURCES = [
  "templates",
  "template-instances",
  "agents",
  "agent-teams",
  "automations",
  "projects",
  "workspaces",
  "workflows",
  "capability-assignments",
  "model-routing-profiles",
] as const;

export const TEMPLATE_REQUIRED_COMMANDS = [
  "template.create",
  "template.create-from-agent",
  "template.create-from-agent-team",
  "template.create-from-workflow",
  "template.create-from-capability-assignment",
  "template.create-from-model-routing-profile",
  "template.create-from-automation",
  "template.create-from-project",
  "template.create-from-workspaces",
  "template.revise",
  "template.publish",
  "template.clone",
  "template.fork",
  "template.preview",
  "template.apply",
  "template.reapply",
] as const;

export function templateManifestState(
  state: ControlPlaneManifestState,
  manifest: APImanifest | null,
): TemplateManifestState {
  if (state === "loading") return "loading";
  if (state !== "ready" || manifest === null) return "unavailable";
  if (!TEMPLATE_REQUIRED_RESOURCES.every((resource) => manifest.resources.includes(resource))) {
    return "unavailable";
  }
  if (
    manifest.commands === undefined
    || !TEMPLATE_REQUIRED_COMMANDS.every((command) => manifest.commands?.includes(command))
  ) {
    return "unavailable";
  }
  return "available";
}
