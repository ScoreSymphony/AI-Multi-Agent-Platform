import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RunFailureDiagnostics, RunWorkspaceBinding } from "./Pages";

vi.mock("../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
  matchPath: () => null,
  useRouter: () => ({ path: "/", navigate: vi.fn() }),
}));

describe("#1333 Run Workspace operator provenance", () => {
  it("links the canonical Workspace and exposes immutable snapshot provenance", () => {
    const html = renderToStaticMarkup(
      <RunWorkspaceBinding
        run={{
          workspace_id: "workspace_test",
          workspace_snapshot_id: "workspace_snapshot_test",
          workspace_content_checksum: "a".repeat(64),
        }}
      />,
    );

    expect(html).toContain('href="/workspaces/workspace_test"');
    expect(html).toContain("Open Workspace");
    expect(html).toContain("workspace_test");
    expect(html).toContain("workspace_snapshot_test");
    expect(html).toContain("a".repeat(64));
  });

  it("renders an explicit absence instead of fabricating a Workspace binding", () => {
    const html = renderToStaticMarkup(
      <RunWorkspaceBinding
        run={{
          workspace_id: undefined,
          workspace_snapshot_id: undefined,
          workspace_content_checksum: undefined,
        }}
      />,
    );

    expect(html).toContain("No canonical Workspace binding is recorded for this Run.");
    expect(html).not.toContain("/workspaces/");
  });
  it("keeps failure class, Workspace provenance and canonical retry navigation together", () => {
    const html = renderToStaticMarkup(
      <>
        <RunFailureDiagnostics
          run={{
            status: "failed",
            task_id: "task_test",
            error: {
              category: "execution",
              code: "run_failed",
              message: "controlled failure",
            },
          }}
        />
        <RunWorkspaceBinding
          run={{
            workspace_id: "workspace_test",
            workspace_snapshot_id: "workspace_snapshot_test",
            workspace_content_checksum: "b".repeat(64),
          }}
        />
      </>,
    );

    expect(html).toContain("execution: run_failed");
    expect(html).toContain("controlled failure");
    expect(html).toContain("Supported next action");
    expect(html).toContain('href="/tasks/task_test"');
    expect(html).toContain("Open Task for retry");
    expect(html).toContain('href="/workspaces/workspace_test"');
    expect(html).toContain("workspace_snapshot_test");
  });

});
