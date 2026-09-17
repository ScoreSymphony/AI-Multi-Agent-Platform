import { describe, expect, it } from "vitest";
import type { CanonicalApplicationInstance } from "../api/applications";
import { applicationActionState } from "./ApplicationsPage";

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
