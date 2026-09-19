import type { AnchorHTMLAttributes, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ControlPlaneError } from "../../api/client";
import { describe, expect, it, vi } from "vitest";
import {
  AutomationClient,
  type CanonicalAutomation,
  type CanonicalAutomationDelivery,
} from "../../api/automations";
import { ControlPlaneCollectionClient } from "../../api/collections";
import type { Page } from "../../api/types";
import { AutomationForm } from "./AutomationForm";
import { DeliveryTable } from "./AutomationTables";
import { AutomationInventoryState, AutomationsPage } from "./AutomationsPage";

vi.mock("../../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

function retryableBackendError(message: string): ControlPlaneError {
  return new ControlPlaneError(503, {
    code: "unavailable",
    category: "backend",
    message,
    request_id: "request_regression_retry",
    correlation_id: "correlation_regression_retry",
    retryable: true,
  });
}

function automation(overrides: Partial<CanonicalAutomation> = {}): CanonicalAutomation {
  return {
    id: "automation_123",
    type: "automation",
    name: "Nightly checks",
    description: "Run deterministic verification",
    project_id: "project_123",
    workspace_id: "workspace_123",
    state: "enabled",
    identity: {
      principal_ref: "user:alice",
      owner_type: "user",
      owner_id: "alice",
    },
    owner_ref: { type: "user", id: "alice" },
    trigger: {
      type: "recurring",
      timezone: "UTC",
      at: "2026-09-17T00:00:00+00:00",
      interval_seconds: 86400,
      event_type: null,
      filters: {},
      webhook_source: null,
      verification_ref: null,
      missed_schedule_policy: "coalesce",
    },
    task_template: {
      title: "Run checks",
      objective: "Verify the platform",
      project_id: "project_123",
      workspace_id: "workspace_123",
      payload: {},
    },
    deduplication_strategy: "delivery_key",
    retry_policy: { max_attempts: 3, base_backoff_seconds: 1 },
    overlap_policy: "skip_while_processing",
    created_at: "2026-09-16T00:00:00Z",
    updated_at: "2026-09-16T01:00:00Z",
    revision: 4,
    last_evaluated_at: "2026-09-16T01:00:00Z",
    next_evaluation_at: "2026-09-17T00:00:00Z",
    ...overrides,
  };
}

function delivery(overrides: Partial<CanonicalAutomationDelivery> = {}): CanonicalAutomationDelivery {
  return {
    id: "delivery_123",
    type: "automation-delivery",
    automation_id: "automation_123",
    trigger_type: "recurring",
    source: "scheduler",
    dedupe_key: "automation_123:2026-09-16",
    fired_at: "2026-09-16T00:00:00Z",
    received_at: "2026-09-16T00:00:01Z",
    payload: {},
    status: "failed",
    attempt: 1,
    generated_task_id: null,
    error_code: "unavailable",
    error_message: "worker unavailable",
    processing_duration_ms: 25,
    owner_ref: { type: "user", id: "alice" },
    project_id: "project_123",
    workspace_id: "workspace_123",
    ...overrides,
  };
}

function page(items: CanonicalAutomation[]): Page<CanonicalAutomation> {
  return { items, next_cursor: null, total: items.length, limit: 50 };
}

describe("Automations page regression coverage", () => {
  it("preserves route composition, initial loading state and the extracted create form", () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("SSR regression coverage must not perform network requests");
    }) as unknown as typeof fetch;
    const html = renderToStaticMarkup(
      <AutomationsPage
        collections={new ControlPlaneCollectionClient({ fetchImpl })}
        automations={new AutomationClient({ fetchImpl })}
      />,
    );

    expect(html).toContain("<h1>Automations</h1>");
    expect(html).toContain("Automation inventory");
    expect(html).toContain("Loading…");
    expect(html).toContain("Create automation");
    expect(html).toContain("Automation project ID");
    expect(html).toContain('type="submit"');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("maps loading, backend error, empty and populated inventory states deterministically", () => {
    const retry = vi.fn();

    expect(renderToStaticMarkup(
      <AutomationInventoryState page={null} error={null} onRetry={retry} />,
    )).toContain("Loading…");

    const errorMarkup = renderToStaticMarkup(
      <AutomationInventoryState page={null} error={retryableBackendError("control plane unavailable")} onRetry={retry} />,
    );
    expect(errorMarkup).toContain('role="alert"');
    expect(errorMarkup).toContain("control plane unavailable");
    expect(errorMarkup).toContain(">Retry</button>");

    expect(renderToStaticMarkup(
      <AutomationInventoryState page={page([])} error={null} onRetry={retry} />,
    )).toContain("No automations configured");

    const staleMarkup = renderToStaticMarkup(
      <AutomationInventoryState page={page([])} error={new Error("refresh failed")} onRetry={retry} />,
    );
    expect(staleMarkup).toContain("refresh failed");
    expect(staleMarkup).toContain("No automations configured");

    const readyMarkup = renderToStaticMarkup(
      <AutomationInventoryState page={page([automation()])} error={null} onRetry={retry} />,
    );
    expect(readyMarkup).toContain("Nightly checks");
    expect(readyMarkup).toContain("/automations/automation_123");
    expect(readyMarkup).toContain("Run checks");
  });

  it("preserves schedule form semantics, labels and disabled submit behavior", () => {
    const html = renderToStaticMarkup(
      <AutomationForm
        initial={automation()}
        submitLabel="Saving…"
        disabled
        onSubmit={vi.fn(async () => undefined)}
      />,
    );

    expect(html).toContain("Trigger type");
    expect(html).toContain("First fire / at (ISO-8601 with offset)");
    expect(html).toContain("Interval seconds");
    expect(html).toContain("Task objective");
    expect(html).toContain('type="submit"');
    expect(html).toContain("disabled");
    expect(html).toContain("Saving…");
  });

  it("keeps retry as a native keyboard-activatable button and disables it while busy", () => {
    const enabled = renderToStaticMarkup(
      <DeliveryTable deliveries={[delivery()]} busy={false} onRetry={vi.fn(async () => undefined)} />,
    );
    expect(enabled).toContain(">Retry</button>");
    expect(enabled).not.toContain("<button disabled=\"\">Retry</button>");

    const disabled = renderToStaticMarkup(
      <DeliveryTable deliveries={[delivery()]} busy onRetry={vi.fn(async () => undefined)} />,
    );
    expect(disabled).toContain("<button disabled=\"\">Retry</button>");
  });
});
