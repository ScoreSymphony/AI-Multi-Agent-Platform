import { useCallback, useEffect, useState } from "react";
import { ControlPlaneCollectionClient } from "../api/collections";
import { AppLink } from "../app/router";
import {
  CanonicalId,
  Card,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

export type CanonicalConfigurationCollection =
  | "workflows"
  | "capability-assignments"
  | "model-routing-profiles";

type CanonicalConfigurationResource = Record<string, unknown> & { id?: unknown };

export function CanonicalConfigurationDetailPage({
  client,
  collection,
  resourceId,
}: {
  client: ControlPlaneCollectionClient;
  collection: CanonicalConfigurationCollection;
  resourceId: string;
}) {
  const [resource, setResource] = useState<CanonicalConfigurationResource | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setResource(await client.get<CanonicalConfigurationResource>(collection, resourceId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, collection, resourceId]);

  useEffect(() => void load(), [load]);

  if (error && !resource) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!resource) return <LoadingState label={`Loading ${labelFor(collection)}…`} />;

  const canonicalId = typeof resource.id === "string" ? resource.id : resourceId;

  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div>
          <p className="eyebrow">Canonical configuration resource</p>
          <h1>{labelFor(collection)}</h1>
          <CanonicalId value={canonicalId} />
        </div>
        <StatusBadge value="read-only" />
      </header>

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      <Card title="Canonical Control Plane projection">
        <p>
          This view is read-only. The owning domain remains authoritative; Templates only keep
          the canonical resource reference and provenance.
        </p>
        <pre>{JSON.stringify(resource, null, 2)}</pre>
      </Card>

      <div className="actions">
        <AppLink href="/templates">Back to Templates</AppLink>
        <button type="button" onClick={() => void load()}>Refresh</button>
      </div>
    </div>
  );
}

function labelFor(collection: CanonicalConfigurationCollection): string {
  if (collection === "workflows") return "Workflow";
  if (collection === "capability-assignments") return "Capability Assignment";
  return "Model Routing Profile";
}
