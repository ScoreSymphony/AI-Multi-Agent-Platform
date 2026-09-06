import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { TemplatePreview, TemplateResourceRef } from "../api/templates";
import { canonicalResourceHref, PreviewReport } from "./TemplatesPage";

function preview(overrides: Partial<TemplatePreview> = {}): TemplatePreview {
  return {
    source: { template_id: "template_123", revision: 4 },
    dependency_order: [{ template_id: "template_123", revision: 4 }],
    missing_required_capability_ids: [],
    missing_optional_capability_ids: [],
    incompatible_capability_versions: [],
    incompatible_optional_capability_versions: [],
    incompatible_platform_versions: [],
    missing_contract_versions: [],
    incompatible_contract_versions: [],
    missing_plugin_ids: [],
    missing_connector_ids: [],
    missing_model_policy_refs: [],
    ungrantable_permissions: [],
    missing_workspace_prerequisites: [],
    unresolved_placeholders: [],
    unresolved_secret_reference_placeholders: [],
    unvalidated_configuration_refs: [],
    missing_optional_dependencies: [],
    missing_handler_types: [],
    privileged_capability_ids: [],
    resource_changes: [],
    applicable: false,
    ...overrides,
  };
}

function resource(resourceType: string, resourceId: string): TemplateResourceRef {
  return { resource_type: resourceType, resource_id: resourceId };
}

describe("Template PreviewReport", () => {
  it("renders required platform, contract and Capability version blockers", () => {
    const markup = renderToStaticMarkup(
      <PreviewReport
        preview={preview({
          incompatible_capability_versions: ["capability.search requires >=2; available 1"],
          incompatible_platform_versions: ["template_123@4:platform requires >=3; available 2"],
          missing_contract_versions: ["template_123@4:agent requires >=2; available unknown"],
          incompatible_contract_versions: ["template_123@4:workflow requires >=3; available 2"],
        })}
      />,
    );

    expect(markup).toContain("Missing or incompatible: Required capability versions");
    expect(markup).toContain("capability.search requires &gt;=2; available 1");
    expect(markup).toContain("Missing or incompatible: Platform version");
    expect(markup).toContain("Missing or incompatible: Missing contract versions");
    expect(markup).toContain("Missing or incompatible: Contract versions");
  });

  it("renders optional Capability version mismatches without treating them as required blockers", () => {
    const markup = renderToStaticMarkup(
      <PreviewReport
        preview={preview({
          applicable: true,
          incompatible_optional_capability_versions: [
            "capability.optional requires >=4; available 3",
          ],
        })}
      />,
    );

    expect(markup).toContain("Optional capability versions incompatible:");
    expect(markup).toContain("capability.optional requires &gt;=4; available 3");
  });
});

describe("Template instantiated resource links", () => {
  it("routes newly integrated owner-domain resources to canonical read-only detail surfaces", () => {
    expect(canonicalResourceHref(resource("workflow", "workflow_123"))).toBe(
      "/workflows/workflow_123",
    );
    expect(
      canonicalResourceHref(resource("capability_assignment", "cap_assignment_123")),
    ).toBe("/capability-assignments/cap_assignment_123");
    expect(
      canonicalResourceHref(resource("model_routing_profile", "routing_profile_123")),
    ).toBe("/model-routing-profiles/routing_profile_123");
  });

  it("keeps unknown resource types unlinked rather than fabricating routes", () => {
    expect(canonicalResourceHref(resource("future_resource", "future_123"))).toBeNull();
  });
});
