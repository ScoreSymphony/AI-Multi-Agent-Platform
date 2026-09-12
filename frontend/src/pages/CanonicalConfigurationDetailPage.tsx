import { useCallback, useEffect, useMemo, useState } from "react";
import { BrowserSessionClient } from "../api/browserSession";
import { ControlPlaneClient } from "../api/client";
import { ControlPlaneCollectionClient } from "../api/collections";
import { ConfigurationClient } from "../api/configuration";
import { AppLink } from "../app/router";
import {
  CanonicalId,
  Card,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import {
  CapabilityAssignmentConfigurationPage,
  RoutingProfileConfigurationPage,
} from "./SingleNodeConfigurationPage";

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
  const session = useMemo(() => new BrowserSessionClient({ baseUrl: client.baseUrl }), [client.baseUrl]);
  const core = useMemo(
    () => new ControlPlaneClient({ baseUrl: client.baseUrl, fetchImpl: session.fetch }),
    [client.baseUrl, session],
  );
  const configuration = useMemo(
    () => new ConfigurationClient({ transport: session.transport }),
    [session],
  );

  if (collection === "model-routing-profiles") {
    return <RoutingProfileConfigurationPage core={core} configuration={configuration} profileId={resourceId} />;
  }
  if (collection === "capability-assignments") {
    return <CapabilityAssignmentConfigurationPage core={core} configuration={configuration} assignmentId={resourceId} />;
  }
  return <ReadOnlyWorkflowPage client={client} collection={collection} resourceId={resourceId} />;
}

function ReadOnlyWorkflowPage({
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
          Workflow editing is outside #696. The owning Workflow domain remains authoritative;
          Templates only keep the canonical resource reference and provenance.
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
