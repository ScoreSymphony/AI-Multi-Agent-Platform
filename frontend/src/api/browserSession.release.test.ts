import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient, type ReleaseVersionSnapshot } from "./browserSession";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const versions: ReleaseVersionSnapshot = {
  platform_release: "0.0.1",
  domain_schema: "1.0",
  api: "v1",
  migration_revision: "baseline",
  plugin_manifest: "1",
  portable_format: "1.0",
  template_schema: "1",
  backup_format: "1",
  worker_protocol: "1.0",
  message_protocol: "1.0",
  adapter_versions: {},
  plugin_interface_versions: {},
};

describe("BrowserSessionClient release status", () => {
  it("queries the authenticated read-only release status route with schema-v2 typing", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/release/status");
      expect(init?.method).toBe("GET");
      expect(init?.credentials).toBe("include");
      return jsonResponse({
        platform_release: "0.0.1",
        versions,
        release_manifest: {
          release_version: "0.0.1",
          release_kind: "patch",
          source_commit: "a".repeat(40),
          created_at: "2026-09-06T18:30:00Z",
          release_notes_ref: "git:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:CHANGELOG.md",
          versions,
          dependency_sets: [
            {
              name: "python-lock",
              ecosystem: "python",
              kind: "lockfile",
              source_ref: "git:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:requirements.lock",
              digest: `sha256:${"b".repeat(64)}`,
            },
          ],
          upstreams: [],
          compatibility: [],
          gates: [
            {
              name: "ci",
              status: "passed",
              evidence: {
                kind: "workflow_run",
                ref: "workflow:run/123",
                source_commit: "a".repeat(40),
                digest: null,
              },
              required: true,
            },
          ],
          sbom_ref: "artifact:sbom.spdx.json",
          provenance_ref: "attestation:release-provenance.json",
          artifact_hashes: { "platform-wheel": `sha256:${"c".repeat(64)}` },
          release_ready: true,
          release_blockers: [],
          release_warnings: [],
        },
        compatibility_inventory: {
          schema_version: "2",
          platform_release: "0.0.1",
          versions,
          generated_from: "upstream/*.yaml + canonical VersionSnapshot",
          last_reviewed_at: "2026-09-06T15:55:00Z",
          components: [],
        },
        update_discovery: {
          mode: "current",
          observed_at: "2026-09-06T18:35:00Z",
          update_available: false,
          candidates: [],
        },
        update_discovery_reviewed_at: "2026-09-06T18:36:00Z",
        release_ready: true,
        operator_warnings: [],
        automatic_production_updates: false,
        production_pin_mutation: "not_permitted_by_discovery",
      });
    });
    const session = new BrowserSessionClient({ fetchImpl, storage: null });

    const status = await session.releaseStatus();

    expect(status.platform_release).toBe("0.0.1");
    expect(status.compatibility_inventory.versions.worker_protocol).toBe("1.0");
    expect(status.release_manifest?.dependency_sets[0].kind).toBe("lockfile");
    expect(status.release_manifest?.gates[0].evidence.kind).toBe("workflow_run");
    expect(status.update_discovery_reviewed_at).toBe("2026-09-06T18:36:00Z");
    expect(status.automatic_production_updates).toBe(false);
  });
});
