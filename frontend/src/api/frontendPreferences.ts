import { createUuid } from "../uuid";
import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue } from "./types";

export interface CanonicalFrontendPreference {
  id: string;
  type: "frontend-preference";
  scope: "user";
  schema_version: number;
  revision: number;
  updated_at: string | null;
  customization: Record<string, JsonValue> | null;
}

export interface FrontendPreferencesClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

const FRONTEND_PREFERENCE_COLLECTION = "frontend-preferences";

export class FrontendPreferencesClient {
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: FrontendPreferencesClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
  }

  async preference(): Promise<CanonicalFrontendPreference> {
    const page = await this.collections.list<CanonicalFrontendPreference>(
      FRONTEND_PREFERENCE_COLLECTION,
      { limit: 1 },
    );
    const preference = page.items[0];
    if (!preference) throw new Error("Canonical frontend preference is unavailable");
    return preference;
  }

  update(
    preferenceId: string,
    customization: Record<string, JsonValue>,
    expectedRevision: number,
  ): Promise<CanonicalFrontendPreference> {
    return this.command("frontend-preference.update", preferenceId, {
      schema_version: 1,
      customization,
      expected_revision: expectedRevision,
    });
  }

  reset(
    preferenceId: string,
    expectedRevision: number,
  ): Promise<CanonicalFrontendPreference> {
    return this.command("frontend-preference.reset", preferenceId, {
      expected_revision: expectedRevision,
    });
  }

  private command<T>(
    command: string,
    resourceRef: string,
    payload: object,
  ): Promise<T> {
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: resourceRef, ...payload },
      idempotencyKey: createUuid(),
    });
  }
}
