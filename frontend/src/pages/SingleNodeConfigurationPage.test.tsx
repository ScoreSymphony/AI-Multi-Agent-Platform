import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { CanonicalCapability, CanonicalCapabilityVersion } from "../api/capabilities";
import { ControlPlaneClient } from "../api/client";
import { ConfigurationClient } from "../api/configuration";
import {
  CapabilityAssignmentConfigurationPage,
  RoutingProfileConfigurationPage,
  ruleForCapability,
} from "./SingleNodeConfigurationPage";

vi.mock("../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

function capabilityVersion(
  overrides: Partial<CanonicalCapabilityVersion> = {},
): CanonicalCapabilityVersion {
  return {
    capability_id: "tool.shell",
    name: "Shell",
    version: "1.0.0",
    description: "Canonical shell capability",
    input_schema: {},
    output_schema: null,
    tags: [],
    safety: "standard",
    side_effects: "none",
    required_permissions: [],
    required_approvals: [],
    required_worker_capabilities: [],
    timeout_seconds: null,
    health: "healthy",
    available: true,
    features: [],
    credential_requirement: "none",
    ...overrides,
  };
}

function capability(
  versions: CanonicalCapabilityVersion[],
): CanonicalCapability {
  return {
    id: "tool.shell",
    type: "capability",
    name: "Shell",
    version_count: versions.length,
    available: true,
    versions,
  };
}

function clients() {
  const fetchImpl = vi.fn(async () => {
    throw new Error("SSR editor coverage must not perform network requests");
  }) as unknown as typeof fetch;
  return {
    core: new ControlPlaneClient({ fetchImpl }),
    configuration: new ConfigurationClient({ fetchImpl }),
  };
}

describe("single-node configuration editors", () => {
  it("derives explicit privilege and approval metadata from every matching capability version", () => {
    const canonical = capability([
      capabilityVersion(),
      capabilityVersion({
        version: "2.0.0",
        safety: "restricted",
        side_effects: "external",
        required_permissions: ["workspace.write"],
        required_approvals: ["human"],
        credential_requirement: "required",
      }),
    ]);

    expect(ruleForCapability(canonical.id, [canonical])).toMatchObject({
      capability_id: canonical.id,
      privileged: true,
      approval_required: true,
    });
  });

  it("does not elevate a standard capability without credentials or approvals", () => {
    const canonical = capability([capabilityVersion()]);

    expect(ruleForCapability(canonical.id, [canonical])).toMatchObject({
      privileged: false,
      approval_required: false,
    });
  });

  it("renders a purpose-built Model Routing Profile editor", () => {
    const { core, configuration } = clients();
    const html = renderToStaticMarkup(
      <RoutingProfileConfigurationPage core={core} configuration={configuration} />,
    );

    expect(html).toContain("Create Model Routing Profile");
    expect(html).toContain("Explicit model");
    expect(html).toContain("Preferred models");
    expect(html).toContain("Local only");
    expect(html).toContain("Create routing profile");
  });

  it("renders target pickers, policy buckets and the approval retry input for assignments", () => {
    const { core, configuration } = clients();
    const html = renderToStaticMarkup(
      <CapabilityAssignmentConfigurationPage core={core} configuration={configuration} />,
    );

    expect(html).toContain("Create Capability Assignment");
    expect(html).toContain("Target resource");
    expect(html).toContain("Required capabilities");
    expect(html).toContain("Allowed capabilities");
    expect(html).toContain("Denied capabilities");
    expect(html).toContain("Security implications");
    expect(html).toContain("Approval ID");
    expect(html).toContain("Create assignment");
  });
});
