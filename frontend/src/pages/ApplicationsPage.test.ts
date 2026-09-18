import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { CanonicalApplication, CanonicalApplicationConfigurationField, CanonicalApplicationInstance } from "../api/applications";
import { ApplicationConfigurationForm, applicationActionState, parseApplicationConfigurationValue } from "./ApplicationsPage";

function instance(
  desired_state: CanonicalApplicationInstance["desired_state"],
  observed_state: CanonicalApplicationInstance["observed_state"],
): CanonicalApplicationInstance {
  return {
    id: "application_instance_123e4567-e89b-42d3-a456-426614174000",
    type: "application-instance",
    application_ref: "application_123e4567-e89b-42d3-a456-426614174001@1.0.0",
    application_id: "application_123e4567-e89b-42d3-a456-426614174001",
    application_version: "1.0.0",
    runtime_id: "local.process",
    node_id: null,
    desired_state,
    observed_state,
    health: "unknown",
    configuration: {},
    secret_bindings: {},
    volume_bindings: [],
    service_states: [],
    endpoints: [],
    open: null,
    revision: 1,
    created_at: "2026-09-17T00:00:00+00:00",
    updated_at: "2026-09-17T00:00:00+00:00",
  };
}



function field(
  name: string,
  value_type: CanonicalApplicationConfigurationField["value_type"],
  overrides: Partial<CanonicalApplicationConfigurationField> = {},
): CanonicalApplicationConfigurationField {
  return {
    name,
    value_type,
    required: false,
    default: null,
    mutable: true,
    environment_variable: null,
    ...overrides,
  };
}

function application(
  configuration: CanonicalApplicationConfigurationField[],
): CanonicalApplication {
  return {
    id: "application_demo@1.0.0",
    type: "application",
    application_id: "application_demo",
    version: "1.0.0",
    name: "Demo",
    runtime_id: "local.process",
    source_ref: null,
    installed_at: "2026-09-17T00:00:00+00:00",
    manifest: {
      schema_version: "1",
      application_id: "application_demo",
      name: "Demo",
      version: "1.0.0",
      description: "demo",
      services: [],
      volumes: [],
      configuration,
      secrets: [],
      resources: {},
      ui: null,
      resource_associations: [],
      maturity: "beta",
      runtime_requirements: [],
    },
    provenance: {},
  };
}

describe("applicationActionState", () => {
  it("offers start and safe removal for stopped intent", () => {
    expect(applicationActionState(instance("stopped", "stopped"))).toEqual({
      start: true,
      stop: false,
      restart: false,
      reconcile: true,
      remove: true,
    });
  });

  it("offers stop and restart for running intent", () => {
    expect(applicationActionState(instance("running", "running"))).toEqual({
      start: false,
      stop: true,
      restart: true,
      reconcile: true,
      remove: false,
    });
  });

  it("disables lifecycle mutation after removal", () => {
    expect(applicationActionState(instance("removed", "removed"))).toEqual({
      start: false,
      stop: false,
      restart: false,
      reconcile: false,
      remove: false,
    });
  });
});


describe("Application configuration Web contract", () => {
  it("parses manifest-typed configuration values without inventing backend types", () => {
    expect(parseApplicationConfigurationValue(field("label", "string"), "worker")).toBe("worker");
    expect(parseApplicationConfigurationValue(field("workers", "integer"), "4")).toBe(4);
    expect(parseApplicationConfigurationValue(field("ratio", "number"), "1.5")).toBe(1.5);
    expect(parseApplicationConfigurationValue(field("enabled", "boolean"), "true")).toBe(true);
    expect(parseApplicationConfigurationValue(field("optional", "string"), "")).toBeNull();

    expect(() => parseApplicationConfigurationValue(field("workers", "integer"), "1.5"))
      .toThrow("must be an integer");
    expect(() => parseApplicationConfigurationValue(
      field("required", "string", { required: true }),
      "",
    )).toThrow("is required");
  });

  it("renders only mutable manifest configuration fields as editable controls", () => {
    const current = instance("stopped", "stopped");
    current.configuration.label = "before";
    current.configuration.workers = 2;
    current.configuration.fixed = "stable";

    const html = renderToStaticMarkup(
      createElement(ApplicationConfigurationForm, {
        application: application([
          field("label", "string", { required: true }),
          field("workers", "integer"),
          field("enabled", "boolean", { default: true }),
          field("fixed", "string", { mutable: false }),
        ]),
        instance: current,
        busy: false,
        onConfigure: vi.fn(),
      }),
    );

    expect(html).toContain('name="label"');
    expect(html).toContain('name="workers"');
    expect(html).toContain('name="enabled"');
    expect(html).not.toContain('name="fixed"');
    expect(html).toContain("Save configuration");
  });
});
