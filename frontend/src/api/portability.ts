import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

export const PORTABILITY_PACKAGE_COLLECTION = "portability-packages";
export const PORTABILITY_PREVIEW_COLLECTION = "portability-import-previews";
export const PORTABILITY_REPORT_COLLECTION = "portability-import-reports";
export const PORTABILITY_RESOURCES = [
  PORTABILITY_PACKAGE_COLLECTION,
  PORTABILITY_PREVIEW_COLLECTION,
  PORTABILITY_REPORT_COLLECTION,
] as const;

export interface PortabilityResourceSelection {
  resource_type: string;
  resource_id: string;
}

export interface PortabilityPackageInspection {
  id: string;
  package_id: string;
  checksum: string;
  compatible: boolean;
  compatibility_issues: string[];
  resource_count: number;
  package: JsonValue;
}

export interface PortabilityPreviewResource {
  resource_type: string;
  source_id: string;
  target_id: string;
  resource_version: string;
  id_policy: string;
}

export interface PortabilityDependencyFinding {
  requested_by: string;
  kind: string;
  identifier: string;
  required: boolean;
  version_constraint: string | null;
  purpose: string | null;
}

export interface PortabilityConflict {
  kind: string;
  resource_type: string;
  resource_id: string;
  detail: string;
}

export interface PortabilitySecurityFinding extends PortabilityConflict {
  blocking: boolean;
}

export interface PortabilityImportPreview {
  id: string;
  preview_id: string;
  package_id: string;
  package_checksum: string;
  ready: boolean;
  package_compatible: boolean;
  compatibility_issues: string[];
  resources: PortabilityPreviewResource[];
  import_order: Array<{ resource_type: string; resource_id: string }>;
  id_mapping: Array<{ resource_type: string; source_id: string; target_id: string }>;
  missing_dependencies: PortabilityDependencyFinding[];
  optional_missing_dependencies: PortabilityDependencyFinding[];
  conflicts: PortabilityConflict[];
  security_findings: PortabilitySecurityFinding[];
}

export interface PortabilityImportReport {
  id: string;
  report_id: string;
  package_id: string;
  preview_id: string;
  status: string;
  result: {
    package_checksum: string;
    resources: Array<{
      resource_type: string;
      source_id: string;
      target_id: string;
      resource_version: string;
    }>;
  };
}

export interface PortabilityClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class PortabilityClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: PortabilityClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
  }

  listPackages(query: ListQuery = {}): Promise<Page<PortabilityPackageInspection>> {
    return this.collections.list<PortabilityPackageInspection>(PORTABILITY_PACKAGE_COLLECTION, query);
  }

  getPackage(packageId: string): Promise<PortabilityPackageInspection> {
    return this.collections.get<PortabilityPackageInspection>(
      PORTABILITY_PACKAGE_COLLECTION,
      requireNonBlank(packageId, "Package ID"),
    );
  }

  listPreviews(query: ListQuery = {}): Promise<Page<PortabilityImportPreview>> {
    return this.collections.list<PortabilityImportPreview>(PORTABILITY_PREVIEW_COLLECTION, query);
  }

  getPreview(previewId: string): Promise<PortabilityImportPreview> {
    return this.collections.get<PortabilityImportPreview>(
      PORTABILITY_PREVIEW_COLLECTION,
      requireNonBlank(previewId, "Preview ID"),
    );
  }

  listReports(query: ListQuery = {}): Promise<Page<PortabilityImportReport>> {
    return this.collections.list<PortabilityImportReport>(PORTABILITY_REPORT_COLLECTION, query);
  }

  getReport(reportId: string): Promise<PortabilityImportReport> {
    return this.collections.get<PortabilityImportReport>(
      PORTABILITY_REPORT_COLLECTION,
      requireNonBlank(reportId, "Import report ID"),
    );
  }

  exportPackage(
    resources: PortabilityResourceSelection[],
    metadata?: Record<string, JsonValue>,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<PortabilityPackageInspection> {
    if (resources.length === 0) throw new Error("At least one export resource is required");
    const normalized = resources.map((resource) => ({
      resource_type: requireNonBlank(resource.resource_type, "Resource type"),
      resource_id: requireNonBlank(resource.resource_id, "Resource ID"),
    }));
    return this.command<PortabilityPackageInspection>(
      "portability.export",
      "portability",
      {
        resources: normalized,
        ...(metadata === undefined ? {} : { metadata }),
      },
      idempotencyKey,
    );
  }

  validatePackage(
    packageDocument: JsonValue,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<PortabilityPackageInspection> {
    if (!isJsonObject(packageDocument)) throw new Error("Portable package must be a JSON object");
    return this.command<PortabilityPackageInspection>(
      "portability.package.validate",
      "portability",
      { package: packageDocument },
      idempotencyKey,
    );
  }

  previewPackage(
    packageId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<PortabilityImportPreview> {
    return this.command<PortabilityImportPreview>(
      "portability.preview",
      requireNonBlank(packageId, "Package ID"),
      {},
      idempotencyKey,
    );
  }

  importPreview(
    previewId: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<PortabilityImportReport> {
    return this.command<PortabilityImportReport>(
      "portability.import",
      requireNonBlank(previewId, "Preview ID"),
      {},
      idempotencyKey,
    );
  }

  private command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    const key = requireNonBlank(idempotencyKey, "Portability idempotency key");
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: resourceRef, ...payload },
      idempotencyKey: key,
    });
  }
}

function requireNonBlank(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} is required`);
  return normalized;
}

function isJsonObject(value: JsonValue): boolean {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
