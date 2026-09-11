import type { ReactNode } from "react";
import type { APImanifest } from "../../api/types";
import { LoadingState } from "../../components/States";
import { UnavailablePage } from "../../pages/Pages";
import { templateManifestState } from "../templateManifest";

export type ManifestState = "loading" | "ready" | "unavailable";
export type ManifestResourceState = "loading" | "available" | "unavailable";

export function ManifestResourcePage({
  state,
  manifest,
  label,
  resource,
  children,
}: {
  state: ManifestState;
  manifest: APImanifest | null;
  label: string;
  resource: string;
  children: ReactNode;
}) {
  const resourceState = resource === "templates"
    ? templateManifestState(state, manifest)
    : manifestResourceState(state, manifest, resource);
  if (resourceState === "loading") return <LoadingState label={`Checking ${label} availability…`} />;
  if (resourceState === "unavailable") {
    return <UnavailablePage item={{ label, apiResource: resource }} manifest={manifest} />;
  }
  return children;
}

export function ManifestResourcesPage({
  state,
  manifest,
  label,
  resources,
  children,
}: {
  state: ManifestState;
  manifest: APImanifest | null;
  label: string;
  resources: readonly string[];
  children: ReactNode;
}) {
  const resourceState = manifestResourcesState(state, manifest, resources);
  if (resourceState === "loading") return <LoadingState label={`Checking ${label} availability…`} />;
  if (resourceState === "unavailable") {
    const missingResource = resources.find((resource) => !manifest?.resources.includes(resource));
    return <UnavailablePage item={{ label, apiResource: missingResource ?? resources[0] }} manifest={manifest} />;
  }
  return children;
}

export function manifestResourceState(
  state: ManifestState,
  manifest: APImanifest | null,
  resource: string,
): ManifestResourceState {
  if (state === "loading") return "loading";
  if (state !== "ready" || manifest === null) return "unavailable";
  return manifest.resources.includes(resource) ? "available" : "unavailable";
}

export function manifestResourcesState(
  state: ManifestState,
  manifest: APImanifest | null,
  resources: readonly string[],
): ManifestResourceState {
  if (state === "loading") return "loading";
  if (state !== "ready" || manifest === null) return "unavailable";
  return resources.every((resource) => manifest.resources.includes(resource)) ? "available" : "unavailable";
}

export function apiStatusLabel(state: ManifestState, manifest: APImanifest | null): string {
  if (state === "ready" && manifest !== null) return `/api/${manifest.api_version}`;
  if (state === "unavailable") return "API unavailable";
  return "Checking API";
}
