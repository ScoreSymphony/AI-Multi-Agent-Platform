import { createUuid } from "../uuid";
import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

export type ApplicationDesiredState = "stopped" | "running" | "removed";
export type ApplicationObservedState =
  | "preparing"
  | "stopped"
  | "starting"
  | "running"
  | "degraded"
  | "unhealthy"
  | "stopping"
  | "failed"
  | "removing"
  | "removed";
export type ApplicationHealth = "unknown" | "healthy" | "degraded" | "unhealthy";
export type ApplicationOpenMode = "embedded" | "external";
export type ApplicationConfigValueType = "string" | "integer" | "number" | "boolean";

export interface CanonicalApplicationConfigurationField {
  name: string;
  value_type: ApplicationConfigValueType;
  required: boolean;
  default: JsonValue;
  mutable: boolean;
  environment_variable: string | null;
}

export interface CanonicalApplicationManifest {
  schema_version: string;
  application_id: string;
  name: string;
  version: string;
  description: string;
  services: Array<Record<string, JsonValue>>;
  volumes: Array<Record<string, JsonValue>>;
  configuration: CanonicalApplicationConfigurationField[];
  secrets: Array<Record<string, JsonValue>>;
  resources: Record<string, JsonValue>;
  ui: Record<string, JsonValue> | null;
  resource_associations: Array<Record<string, JsonValue>>;
  maturity: string;
  runtime_requirements: string[];
}

export interface CanonicalApplication {
  id: string;
  type: "application";
  application_id: string;
  version: string;
  name: string;
  runtime_id: string;
  source_ref: string | null;
  installed_at: string;
  manifest: CanonicalApplicationManifest;
  provenance: Record<string, JsonValue>;
}

export interface CanonicalApplicationServiceState {
  service_id: string;
  observed_state: ApplicationObservedState;
  health: ApplicationHealth;
  message: string | null;
}

export interface CanonicalApplicationEndpoint {
  endpoint_ref: string;
  uri: string;
  exposure: "internal" | "user" | "api" | "metrics";
}

export interface CanonicalApplicationOpenTarget {
  endpoint_ref: string;
  uri: string;
  open_mode: ApplicationOpenMode;
}

export interface CanonicalSecretReference {
  provider: string;
  secret_id: string;
  scope: string;
  version: string | null;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalApplicationVolumeBinding {
  volume_name: string;
  kind: "workspace" | "persistent" | "ephemeral";
  source_ref: string;
  read_only: boolean;
}

export interface CanonicalApplicationInstance {
  id: string;
  type: "application-instance";
  application_ref: string;
  application_id: string;
  application_version: string;
  runtime_id: string;
  node_id: string | null;
  desired_state: ApplicationDesiredState;
  observed_state: ApplicationObservedState;
  health: ApplicationHealth;
  configuration: Record<string, JsonValue>;
  secret_bindings: Record<string, CanonicalSecretReference>;
  volume_bindings: CanonicalApplicationVolumeBinding[];
  service_states: CanonicalApplicationServiceState[];
  endpoints: CanonicalApplicationEndpoint[];
  open: CanonicalApplicationOpenTarget | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface CanonicalApplicationLogEntry {
  timestamp: string;
  service_id: string | null;
  level: string;
  message: string;
}

export interface CanonicalApplicationLogStream {
  id: string;
  type: "application-log-stream";
  instance_id: string;
  application_id: string;
  application_version: string;
  runtime_id: string;
  service_ids: string[];
  entries?: CanonicalApplicationLogEntry[];
}

export interface ApplicationsClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

const APPLICATIONS = "applications";
const INSTANCES = "application-instances";
const LOGS = "application-logs";

export class ApplicationsClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: ApplicationsClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
  }

  listApplications(query: ListQuery = {}): Promise<Page<CanonicalApplication>> {
    return this.collections.list<CanonicalApplication>(APPLICATIONS, query);
  }

  getApplication(applicationRef: string): Promise<CanonicalApplication> {
    return this.collections.get<CanonicalApplication>(APPLICATIONS, requireRef(applicationRef));
  }

  listInstances(query: ListQuery = {}): Promise<Page<CanonicalApplicationInstance>> {
    return this.collections.list<CanonicalApplicationInstance>(INSTANCES, query);
  }

  getInstance(instanceId: string): Promise<CanonicalApplicationInstance> {
    return this.collections.get<CanonicalApplicationInstance>(INSTANCES, requireRef(instanceId));
  }

  getLogs(instanceId: string): Promise<CanonicalApplicationLogStream> {
    return this.collections.get<CanonicalApplicationLogStream>(LOGS, requireRef(instanceId));
  }

  start(instanceId: string): Promise<CanonicalApplicationInstance> {
    return this.command("application.start", instanceId);
  }

  stop(instanceId: string): Promise<CanonicalApplicationInstance> {
    return this.command("application.stop", instanceId);
  }

  restart(instanceId: string): Promise<CanonicalApplicationInstance> {
    return this.command("application.restart", instanceId);
  }

  reconcile(instanceId: string): Promise<CanonicalApplicationInstance> {
    return this.command("application.reconcile", instanceId);
  }

  remove(instanceId: string): Promise<CanonicalApplicationInstance> {
    return this.command("application.remove", instanceId);
  }

  configure(
    instanceId: string,
    configuration: Record<string, JsonValue>,
  ): Promise<CanonicalApplicationInstance> {
    return this.command("application.configure", instanceId, { configuration });
  }

  private command(
    command: string,
    instanceId: string,
    payload: Record<string, JsonValue> = {},
  ): Promise<CanonicalApplicationInstance> {
    return this.transport.request<CanonicalApplicationInstance>(
      `/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        body: { resource_ref: requireRef(instanceId), ...payload },
        idempotencyKey: createUuid(),
      },
    );
  }
}

function requireRef(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("application reference is required");
  return normalized;
}
