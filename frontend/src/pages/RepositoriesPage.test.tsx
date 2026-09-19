import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RepositoryCollectionClient } from "../api/repositories";
import { RepositoriesPage } from "./RepositoriesPage";

describe("RepositoriesPage", () => {
  it("renders canonical repository registration and discovery workflows", () => {
    const fetchImpl = vi.fn();
    const html = renderToStaticMarkup(
      <RepositoriesPage
        client={new RepositoryCollectionClient({ fetchImpl })}
        management={{ attachLocal: true, discover: true, detach: true }}
      />,
    );

    expect(html).toContain("Repositories");
    expect(html).toContain("Attach managed local repository");
    expect(html).toContain("Discover provider repositories");
    expect(html).toContain('name="project_id"');
    expect(html).toContain('name="connection_id"');
    expect(html).toContain('name="provider_id"');
    expect(html).toContain("arbitrary host");
    expect(html).toContain("Provider-native");
  });

  it("keeps read-only repository access while optional management commands are absent", () => {
    const fetchImpl = vi.fn();
    const html = renderToStaticMarkup(
      <RepositoriesPage
        client={new RepositoryCollectionClient({ fetchImpl })}
        management={{ attachLocal: false, discover: false, detach: false }}
      />,
    );

    expect(html).toContain("Repository inventory");
    expect(html).toContain("Local repository management unavailable");
    expect(html).toContain("Repository provider discovery unavailable");
    expect(html).not.toContain('name="project_id"');
    expect(html).not.toContain('name="provider_id"');
  });

});
