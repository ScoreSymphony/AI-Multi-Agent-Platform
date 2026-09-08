import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RegistryClient } from "../api/registry";
import { MarketplacePage } from "./MarketplacePage";

describe("MarketplacePage", () => {
  it("renders the technical Marketplace as the default product surface", () => {
    const client = new RegistryClient({ fetchImpl: vi.fn() });
    const html = renderToStaticMarkup(<MarketplacePage client={client} />);

    expect(html).toContain("Marketplace");
    expect(html).toContain("Technical component ecosystem");
    expect(html).toContain("Technical categories");
    expect(html).toContain("All technical components");
    expect(html).toContain("Code intelligence");
    expect(html).toContain("Coding agents");
    expect(html).toContain("Agent frameworks");
    expect(html).toContain("Specification &amp; skills");
    expect(html).toContain("Memory &amp; context");
    expect(html).toContain("Evaluation");
    expect(html).toContain("Security");
    expect(html).toContain("Browser &amp; execution");
    expect(html).toContain("Inference runtimes");
    expect(html).toContain("Retrieval");
    expect(html).toContain("Model &amp; dataset tooling");
    expect(html).toContain("Music AI");
    expect(html).toContain("All Registry assets");
    expect(html).toContain("Technical components only");
    expect(html).toContain("All types");
    expect(html).toContain("All trust states");
    expect(html).toContain("lifecycle:candidate");
    expect(html).toContain("code-intelligence");
    expect(html).toContain("License");
    expect(html).toContain("Publisher");
    expect(html).toContain("Required capability");
    expect(html).toContain("Platform version");
    expect(html).toContain("Updates only");
    expect(html).toContain("Loading Marketplace");
  });
});
