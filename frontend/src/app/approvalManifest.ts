import {
  APPROVAL_DECISION_COMMANDS,
} from "../api/approvals";
import type { APImanifest } from "../api/types";

export type ApprovalDecisionManifestState = "loading" | "available" | "unavailable";
export type ControlPlaneManifestState = "loading" | "ready" | "unavailable";

export function approvalDecisionManifestState(
  state: ControlPlaneManifestState,
  manifest: APImanifest | null,
): ApprovalDecisionManifestState {
  if (state === "loading") return "loading";
  if (state !== "ready" || manifest === null) return "unavailable";
  if (!manifest.resources.includes("approvals")) return "unavailable";
  if (
    manifest.commands === undefined
    || !APPROVAL_DECISION_COMMANDS.every((command) => manifest.commands?.includes(command))
  ) {
    return "unavailable";
  }
  return "available";
}
