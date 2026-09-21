import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { TraceNode } from "../../api/trace";
import { TraceContext } from "./TraceExplorer";

vi.mock("../../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

function node(context: Record<string, string>): TraceNode {
  return {
    id: "trace_node_test",
    type: "trace-node",
    kind: "span",
    name: "test",
    trace_id: "trace_test",
    span_id: "span_test",
    parent_id: null,
    parent_span_id: null,
    timestamp: "2026-09-21T00:00:00Z",
    finished_at: null,
    duration_seconds: null,
    outcome: "succeeded",
    failure: null,
    context,
    attributes: {},
    async_links: [],
    parallel_with: [],
    usage: [],
    resources: [],
  };
}

describe("#1333 trace Workspace correlation", () => {
  it("renders canonical Workspace context as a navigable resource", () => {
    const html = renderToStaticMarkup(
      <TraceContext node={node({ workspace_id: "workspace_test", step_id: "step_test" })} />,
    );

    expect(html).toContain('href="/workspaces/workspace_test"');
    expect(html).toContain("workspace_test");
    expect(html).toContain("step: step_test");
  });

  it("falls back to the public Run binding when telemetry omits Workspace context", () => {
    const html = renderToStaticMarkup(
      <TraceContext
        node={node({ run_id: "run_test", step_id: "step_test" })}
        workspaceId="workspace_bound"
      />,
    );

    expect(html).toContain('href="/workspaces/workspace_bound"');
    expect(html).toContain("workspace_bound");
    expect(html).toContain("step: step_test");
  });

  it("keeps the existing Task-scoped fallback when Workspace context is absent", () => {
    const html = renderToStaticMarkup(<TraceContext node={node({})} />);
    expect(html).toContain("Task-scoped");
    expect(html).not.toContain("/workspaces/");
  });
});
