import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "../api/client";
import { ComputeClient } from "../api/compute";
import { CapabilitiesPage } from "./CapabilitiesPage";
import { ComputePage } from "./ComputePage";
import { ModelsPage } from "./ModelInventoryPage";

describe("#1234 intelligence, tools and compute loading coverage", () => {
  it("keeps the Models surface and its independent loading sections visible", () => {
    const fetchSpy = vi.fn();
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const markup = renderToStaticMarkup(<ModelsPage client={client} />);

    expect(markup).toContain("Models &amp; providers");
    expect(markup).toContain("Model Routing Profiles");
    expect(markup).toContain("Canonical models");
    expect(markup).toContain("Provider instances");
    expect(markup).toContain("Loading");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("keeps Tools, Capability assignments and the MCP boundary visible while loading", () => {
    const fetchSpy = vi.fn();
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const markup = renderToStaticMarkup(<CapabilitiesPage client={client} />);

    expect(markup).toContain("Tools &amp; capabilities");
    expect(markup).toContain("MCP boundary");
    expect(markup).toContain("Capability Assignments");
    expect(markup).toContain("Canonical capabilities");
    expect(markup).toContain("Capability providers");
    expect(markup).toContain("Loading");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("keeps Nodes, Workers and Worker Jobs independently visible while loading", () => {
    const fetchSpy = vi.fn();
    const client = new ComputeClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const markup = renderToStaticMarkup(<ComputePage client={client} />);

    expect(markup).toContain("Distributed runtime");
    expect(markup).toContain("Nodes");
    expect(markup).toContain("Workers");
    expect(markup).toContain("Worker jobs");
    expect(markup).toContain("Loading Nodes");
    expect(markup).toContain("Loading Workers");
    expect(markup).toContain("Loading Worker Jobs");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
