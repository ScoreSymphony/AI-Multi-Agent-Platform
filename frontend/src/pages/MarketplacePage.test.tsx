import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RegistryClient } from "../api/registry";
import { MarketplacePage } from "./MarketplacePage";

describe("MarketplacePage", () => {
  it("renders the graphical Registry discovery and guarded activation surface", () => {
    const client = new RegistryClient({ fetchImpl: vi.fn() });
    const html = renderToStaticMarkup(<MarketplacePage client={client} />);

    expect(html).toContain("Marketplace");
    expect(html).toContain("Discover");
    expect(html).toContain("Search");
    expect(html).toContain("All types");
    expect(html).toContain("All trust states");
    expect(html).toContain("Tags");
    expect(html).toContain("Categories");
    expect(html).toContain("License");
    expect(html).toContain("Publisher");
    expect(html).toContain("Required capability");
    expect(html).toContain("Platform version");
    expect(html).toContain("Updates only");
    expect(html).toContain("Loading Marketplace");
  });
});
