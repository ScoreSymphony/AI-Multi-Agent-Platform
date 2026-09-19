import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  PortabilityClient,
  type PortabilityImportPreview,
  type PortabilityImportReport,
  type PortabilityPackageInspection,
} from "../api/portability";
import type { JsonValue, Page } from "../api/types";
import { AppLink, useRouter } from "../app/router";
import {
  CanonicalId,
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

export type PortabilityDetailKind = "package" | "preview" | "report";

export function ImportExportPage({ client }: { client: PortabilityClient }) {
  const [packages, setPackages] = useState<Page<PortabilityPackageInspection> | null>(null);
  const [previews, setPreviews] = useState<Page<PortabilityImportPreview> | null>(null);
  const [reports, setReports] = useState<Page<PortabilityImportReport> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [mutationError, setMutationError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [resourceType, setResourceType] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [packageDocument, setPackageDocument] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextPackages, nextPreviews, nextReports] = await Promise.all([
        client.listPackages({ limit: 100 }),
        client.listPreviews({ limit: 100 }),
        client.listReports({ limit: 100 }),
      ]);
      setPackages(nextPackages);
      setPreviews(nextPreviews);
      setReports(nextReports);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const exportResource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setMutationError(null);
    try {
      await client.exportPackage([
        portabilitySelection(resourceType, resourceId),
      ]);
      setResourceType("");
      setResourceId("");
      await load();
    } catch (nextError) {
      setMutationError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const validatePackage = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setMutationError(null);
    try {
      await client.validatePackage(parsePackageDocument(packageDocument));
      setPackageDocument("");
      await load();
    } catch (nextError) {
      setMutationError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const preview = async (packageId: string) => {
    setBusy(true);
    setMutationError(null);
    try {
      await client.previewPackage(packageId);
      await load();
    } catch (nextError) {
      setMutationError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const importPreview = async (previewItem: PortabilityImportPreview) => {
    if (!previewItem.ready) return;
    if (
      typeof window !== "undefined"
      && !window.confirm(
        `Import the exact server-owned preview ${previewItem.preview_id}? This may create canonical resources.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setMutationError(null);
    try {
      await client.importPreview(previewItem.preview_id);
      await load();
    } catch (nextError) {
      setMutationError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const loading = packages === null || previews === null || reports === null;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical portability</p>
        <h1>Import / Export</h1>
        <p>
          Export canonical resources, validate portable package documents, inspect the server-owned
          import plan and apply only an exact accepted preview through the versioned Control Plane.
        </p>
      </header>

      <DegradedState
        title="Import plans remain server-owned"
        detail="The browser can select source resources and inspect package, dependency, conflict and security evidence. It cannot submit an ID mapping, mutation order or trusted destination plan."
      />

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {mutationError ? <ErrorState error={mutationError} onRetry={() => setMutationError(null)} /> : null}

      <div className="grid-two">
        <Card title="Export canonical resource">
          <form className="form-grid" onSubmit={exportResource}>
            <label>
              Resource type
              <input
                required
                value={resourceType}
                onChange={(event) => setResourceType(event.target.value)}
                placeholder="agent"
              />
            </label>
            <label>
              Canonical resource ID
              <input
                required
                value={resourceId}
                onChange={(event) => setResourceId(event.target.value)}
                placeholder="agent_…"
              />
            </label>
            <button className="primary" disabled={busy} type="submit">
              {busy ? "Working…" : "Create portable package"}
            </button>
          </form>
        </Card>

        <Card title="Validate incoming package">
          <form className="stack" onSubmit={validatePackage}>
            <label>
              Portable package JSON
              <textarea
                required
                rows={10}
                value={packageDocument}
                onChange={(event) => setPackageDocument(event.target.value)}
                placeholder='{"manifest":{…},"resources":[…],"checksum":"…"}'
              />
            </label>
            <button className="primary" disabled={busy} type="submit">
              {busy ? "Working…" : "Validate package"}
            </button>
          </form>
        </Card>
      </div>

      <Card title="Portable packages">
        {loading ? <LoadingState label="Loading portable packages…" /> : (
          <PackageTable packages={packages.items} busy={busy} onPreview={preview} />
        )}
      </Card>

      <Card title="Import previews">
        {loading ? <LoadingState label="Loading import previews…" /> : (
          <PreviewTable previews={previews.items} busy={busy} onImport={importPreview} />
        )}
      </Card>

      <Card title="Import reports">
        {loading ? <LoadingState label="Loading import reports…" /> : (
          <ReportTable reports={reports.items} />
        )}
      </Card>

      <div className="actions">
        <button disabled={busy} onClick={() => void load()}>Refresh</button>
      </div>
    </div>
  );
}

export function PortabilityDetailPage({
  client,
  kind,
  resourceId,
}: {
  client: PortabilityClient;
  kind: PortabilityDetailKind;
  resourceId: string;
}) {
  const { navigate } = useRouter();
  const [value, setValue] = useState<
    PortabilityPackageInspection | PortabilityImportPreview | PortabilityImportReport | null
  >(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = kind === "package"
        ? await client.getPackage(resourceId)
        : kind === "preview"
          ? await client.getPreview(resourceId)
          : await client.getReport(resourceId);
      setValue(next);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, kind, resourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const previewPackage = async () => {
    if (kind !== "package") return;
    setBusy(true);
    try {
      const preview = await client.previewPackage(resourceId);
      navigate(`/import-export/previews/${encodeURIComponent(preview.preview_id)}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const applyPreview = async () => {
    if (kind !== "preview" || !value || !("ready" in value) || !value.ready) return;
    if (
      typeof window !== "undefined"
      && !window.confirm(
        `Import the exact server-owned preview ${resourceId}? This may create canonical resources.`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const report = await client.importPreview(resourceId);
      navigate(`/import-export/reports/${encodeURIComponent(report.report_id)}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error && value === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (value === null) return <LoadingState />;

  const status = kind === "package"
    ? (value as PortabilityPackageInspection).compatible ? "compatible" : "incompatible"
    : kind === "preview"
      ? (value as PortabilityImportPreview).ready ? "ready" : "blocked"
      : (value as PortabilityImportReport).status;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Portability {kind}</p>
          <h1>{detailTitle(kind)}</h1>
          <CanonicalId value={resourceId} />
        </div>
        <StatusBadge value={status} />
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      {kind === "preview" && "ready" in value && !value.ready ? (
        <DegradedState
          title="Import is blocked"
          detail="The canonical preview contains compatibility, dependency, conflict or security findings. Resolve them and create a new preview before importing."
        />
      ) : null}

      <Card title="Canonical projection">
        <pre className="code-block">{JSON.stringify(value, null, 2)}</pre>
      </Card>

      <div className="actions">
        <AppLink href="/import-export">Back to Import / Export</AppLink>
        {kind === "package" ? (
          <button disabled={busy} onClick={() => void previewPackage()}>Create import preview</button>
        ) : null}
        {kind === "preview" && "ready" in value ? (
          <button className="primary" disabled={busy || !value.ready} onClick={() => void applyPreview()}>
            {busy ? "Importing…" : "Import exact preview"}
          </button>
        ) : null}
        <button disabled={busy} onClick={() => void load()}>Refresh</button>
      </div>
    </div>
  );
}

export function portabilitySelection(resourceType: string, resourceId: string) {
  const normalizedType = resourceType.trim();
  const normalizedId = resourceId.trim();
  if (!normalizedType) throw new Error("Resource type is required");
  if (!normalizedId) throw new Error("Resource ID is required");
  return { resource_type: normalizedType, resource_id: normalizedId };
}

export function parsePackageDocument(raw: string): JsonValue {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("Portable package must be valid JSON");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Portable package must be a JSON object");
  }
  return value as JsonValue;
}

function PackageTable({
  packages,
  busy,
  onPreview,
}: {
  packages: PortabilityPackageInspection[];
  busy: boolean;
  onPreview: (packageId: string) => Promise<void>;
}) {
  if (!packages.length) return <EmptyState title="No portable packages" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Package</th><th>Resources</th><th>Compatibility</th><th>Actions</th></tr></thead>
        <tbody>
          {packages.map((item) => (
            <tr key={item.package_id}>
              <td><AppLink href={`/import-export/packages/${encodeURIComponent(item.package_id)}`}><CanonicalId value={item.package_id} /></AppLink></td>
              <td>{item.resource_count}</td>
              <td><StatusBadge value={item.compatible ? "compatible" : "incompatible"} /></td>
              <td><button disabled={busy} onClick={() => void onPreview(item.package_id)}>Preview import</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PreviewTable({
  previews,
  busy,
  onImport,
}: {
  previews: PortabilityImportPreview[];
  busy: boolean;
  onImport: (preview: PortabilityImportPreview) => Promise<void>;
}) {
  if (!previews.length) return <EmptyState title="No import previews" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Preview</th><th>Package</th><th>Status</th><th>Findings</th><th>Actions</th></tr></thead>
        <tbody>
          {previews.map((item) => {
            const findingCount = item.missing_dependencies.length + item.conflicts.length + item.security_findings.length;
            return (
              <tr key={item.preview_id}>
                <td><AppLink href={`/import-export/previews/${encodeURIComponent(item.preview_id)}`}><CanonicalId value={item.preview_id} /></AppLink></td>
                <td><AppLink href={`/import-export/packages/${encodeURIComponent(item.package_id)}`}><CanonicalId value={item.package_id} /></AppLink></td>
                <td><StatusBadge value={item.ready ? "ready" : "blocked"} /></td>
                <td>{findingCount}</td>
                <td><button className={item.ready ? "primary" : undefined} disabled={busy || !item.ready} onClick={() => void onImport(item)}>Import</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ReportTable({ reports }: { reports: PortabilityImportReport[] }) {
  if (!reports.length) return <EmptyState title="No import reports" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Report</th><th>Preview</th><th>Status</th><th>Resources</th></tr></thead>
        <tbody>
          {reports.map((item) => (
            <tr key={item.report_id}>
              <td><AppLink href={`/import-export/reports/${encodeURIComponent(item.report_id)}`}><CanonicalId value={item.report_id} /></AppLink></td>
              <td><AppLink href={`/import-export/previews/${encodeURIComponent(item.preview_id)}`}><CanonicalId value={item.preview_id} /></AppLink></td>
              <td><StatusBadge value={item.status} /></td>
              <td>{item.result.resources.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function detailTitle(kind: PortabilityDetailKind): string {
  if (kind === "package") return "Portable package";
  if (kind === "preview") return "Import preview";
  return "Import report";
}
