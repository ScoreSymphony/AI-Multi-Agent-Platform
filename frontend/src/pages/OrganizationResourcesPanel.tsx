import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  CanonicalCollaborationTeam,
  CanonicalResourceOwnership,
  CanonicalResourceShare,
  OrganizationOwnerType,
} from "../api/organizations";
import { OrganizationClient } from "../api/organizations";
import type { Page } from "../api/types";
import { CanonicalId, Card, EmptyState, ErrorState, LoadingState, StatusBadge } from "../components/States";

const MIRRORED_TYPES = new Set([
  "project",
  "workspace",
  "agent",
  "agent_team",
  "automation",
  "memory",
  "knowledge_source",
  "connection",
  "file",
  "artifact",
]);

interface OrganizationResourcesPanelProps {
  client: OrganizationClient;
  organizationId: string;
  teams: CanonicalCollaborationTeam[];
}

export function OrganizationResourcesPanel({
  client,
  organizationId,
  teams,
}: OrganizationResourcesPanelProps) {
  const [ownerships, setOwnerships] = useState<Page<CanonicalResourceOwnership> | null>(null);
  const [shares, setShares] = useState<Page<CanonicalResourceShare> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextOwnerships, nextShares] = await Promise.all([
        client.listOwnerships(organizationId),
        client.listShares(organizationId),
      ]);
      setOwnerships(nextOwnerships);
      setShares(nextShares);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, organizationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (key: string, operation: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await operation();
      await load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(null);
    }
  };

  const createOwnership = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const resourceType = required(form, "resource_type");
    const resourceId = required(form, "resource_id");
    const ownerType = ownerType(form, "owner_type");
    const ownerId = required(form, "owner_id");
    if (MIRRORED_TYPES.has(resourceType)) {
      setError(new Error(`${resourceType} ownership is managed by its canonical resource API.`));
      return;
    }
    await run("ownership.create", async () => {
      await client.setOwnership({
        resource_type: resourceType,
        resource_id: resourceId,
        owner_ref: { type: ownerType, id: ownerId },
        organization_id: organizationId,
      });
      event.currentTarget.reset();
    });
  };

  const createShare = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const ownershipId = required(form, "ownership_id");
    const ownership = ownerships?.items.find((item) => item.id === ownershipId);
    if (!ownership) throw new Error("Selected ownership record no longer exists.");
    await run("share.create", async () => {
      await client.createShare({
        resource_type: ownership.resource_type,
        resource_id: ownership.resource_id,
        target_ref: {
          type: ownerType(form, "target_type"),
          id: required(form, "target_id"),
        },
        policy_refs: csv(form, "policy_refs"),
        allow_cross_organization: form.get("allow_cross_organization") === "on",
      });
      event.currentTarget.reset();
    });
  };

  const ownershipById = useMemo(
    () => new Map((ownerships?.items ?? []).map((item) => [item.id, item])),
    [ownerships],
  );

  return (
    <div className="stack">
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {!ownerships || !shares ? (
        <LoadingState />
      ) : (
        <>
          <div className="two-column">
            <Card title="Register resource ownership">
              <form className="form-grid" onSubmit={createOwnership}>
                <label>
                  Resource type
                  <input name="resource_type" placeholder="custom-resource" required />
                </label>
                <label>
                  Resource ID
                  <input name="resource_id" placeholder="resource_..." required />
                </label>
                <label>
                  Owner type
                  <select name="owner_type" defaultValue="organization">
                    <option value="organization">organization</option>
                    <option value="team">team</option>
                    <option value="user">user</option>
                    <option value="service">service</option>
                  </select>
                </label>
                <label>
                  Owner ID
                  <input name="owner_id" defaultValue={organizationId} required />
                </label>
                <button className="primary" disabled={busy !== null}>
                  {busy === "ownership.create" ? "Registering…" : "Register ownership"}
                </button>
              </form>
              <p>
                Canonical platform resources such as projects, workspaces, agents, automations,
                memory, knowledge, connections, files and artifacts mirror their owner from the
                owning resource domain. Register or transfer those owners through that canonical
                resource API, not through this generic metadata command.
              </p>
            </Card>

            <Card title="Share resource">
              {ownerships.items.length === 0 ? (
                <EmptyState title="No ownership records available" />
              ) : (
                <form className="form-grid" onSubmit={createShare}>
                  <label>
                    Resource
                    <select name="ownership_id" required defaultValue="">
                      <option value="" disabled>Select a resource</option>
                      {ownerships.items.map((ownership) => (
                        <option key={ownership.id} value={ownership.id}>
                          {ownership.resource_type}: {ownership.resource_id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Target type
                    <select name="target_type" defaultValue="team">
                      <option value="team">team</option>
                      <option value="organization">organization</option>
                      <option value="user">user</option>
                      <option value="service">service</option>
                    </select>
                  </label>
                  <label>
                    Target ID
                    <input name="target_id" placeholder={teams[0]?.id ?? "team_..."} required />
                  </label>
                  <label>
                    Policy refs
                    <input name="policy_refs" placeholder="policy:read" />
                  </label>
                  <label className="checkbox-field">
                    <input name="allow_cross_organization" type="checkbox" />
                    Request cross-organization sharing
                  </label>
                  <button className="primary" disabled={busy !== null}>
                    {busy === "share.create" ? "Sharing…" : "Create share"}
                  </button>
                </form>
              )}
            </Card>
          </div>

          <Card title="Resource ownership">
            <OwnershipTable
              client={client}
              organizationId={organizationId}
              ownerships={ownerships.items}
              teams={teams}
              busy={busy}
              run={run}
            />
          </Card>

          <Card title="Resource shares">
            <ShareTable
              shares={shares.items}
              ownershipById={ownershipById}
              busy={busy}
              onRevoke={(share) =>
                void run(`share.revoke:${share.id}`, () => client.revokeShare(share.id))
              }
            />
          </Card>
        </>
      )}
    </div>
  );
}

function OwnershipTable({
  client,
  organizationId,
  ownerships,
  teams,
  busy,
  run,
}: {
  client: OrganizationClient;
  organizationId: string;
  ownerships: CanonicalResourceOwnership[];
  teams: CanonicalCollaborationTeam[];
  busy: string | null;
  run: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  if (!ownerships.length) return <EmptyState title="No resource ownership records" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Resource</th><th>Owner</th><th>Organization</th><th>Transfer</th></tr>
        </thead>
        <tbody>
          {ownerships.map((ownership) => (
            <OwnershipRow
              key={ownership.id}
              client={client}
              organizationId={organizationId}
              ownership={ownership}
              teams={teams}
              busy={busy}
              run={run}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OwnershipRow({
  client,
  organizationId,
  ownership,
  teams,
  busy,
  run,
}: {
  client: OrganizationClient;
  organizationId: string;
  ownership: CanonicalResourceOwnership;
  teams: CanonicalCollaborationTeam[];
  busy: string | null;
  run: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [targetType, setTargetType] = useState<OrganizationOwnerType>(ownership.owner_type);
  const [targetId, setTargetId] = useState(ownership.owner_id);
  const mirrored = MIRRORED_TYPES.has(ownership.resource_type);
  return (
    <tr>
      <td>
        <strong>{ownership.resource_type}</strong>
        <div><CanonicalId value={ownership.resource_id} /></div>
        <div><CanonicalId value={ownership.id} /></div>
      </td>
      <td>{ownership.owner_type}: {ownership.owner_id}</td>
      <td>{ownership.organization_id ?? "Personal"}</td>
      <td>
        <div className="compact-field">
          <select
            aria-label={`Transfer owner type for ${ownership.resource_id}`}
            value={targetType}
            disabled={mirrored || busy !== null}
            onChange={(event) => setTargetType(event.target.value as OrganizationOwnerType)}
          >
            <option value="organization">organization</option>
            <option value="team">team</option>
            <option value="user">user</option>
            <option value="service">service</option>
          </select>
          {targetType === "team" ? (
            <select
              aria-label={`Transfer owner for ${ownership.resource_id}`}
              value={targetId}
              disabled={mirrored || busy !== null}
              onChange={(event) => setTargetId(event.target.value)}
            >
              <option value="">Select team</option>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>{team.name}</option>
              ))}
            </select>
          ) : (
            <input
              aria-label={`Transfer owner for ${ownership.resource_id}`}
              value={targetId}
              disabled={mirrored || busy !== null}
              onChange={(event) => setTargetId(event.target.value)}
            />
          )}
          <button
            className="secondary"
            disabled={mirrored || busy !== null || !targetId.trim()}
            title={mirrored ? "Ownership is managed by the canonical resource API" : undefined}
            onClick={() =>
              void run(`ownership.transfer:${ownership.id}`, () =>
                client.transferOwnership({
                  resource_type: ownership.resource_type,
                  resource_id: ownership.resource_id,
                  owner_ref: { type: targetType, id: targetId.trim() },
                  organization_id: organizationId,
                }),
              )
            }
          >
            Transfer
          </button>
        </div>
      </td>
    </tr>
  );
}

function ShareTable({
  shares,
  ownershipById,
  busy,
  onRevoke,
}: {
  shares: CanonicalResourceShare[];
  ownershipById: Map<string, CanonicalResourceOwnership>;
  busy: string | null;
  onRevoke: (share: CanonicalResourceShare) => void;
}) {
  if (!shares.length) return <EmptyState title="No resource shares" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Resource</th><th>Target</th><th>Policies</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>
          {shares.map((share) => {
            const ownership = ownershipById.get(share.ownership_id);
            return (
              <tr key={share.id}>
                <td>
                  {ownership ? `${ownership.resource_type}: ${ownership.resource_id}` : share.ownership_id}
                  <div><CanonicalId value={share.id} /></div>
                </td>
                <td>{share.target_type}: {share.target_id}</td>
                <td>{share.policy_refs.join(", ") || "—"}</td>
                <td><StatusBadge value={share.status} /></td>
                <td>
                  <button
                    className="danger"
                    disabled={share.status !== "active" || busy !== null}
                    onClick={() => onRevoke(share)}
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function required(form: FormData, field: string): string {
  const value = String(form.get(field) ?? "").trim();
  if (!value) throw new Error(`${field} is required.`);
  return value;
}

function ownerType(form: FormData, field: string): OrganizationOwnerType {
  const value = required(form, field);
  if (value !== "user" && value !== "organization" && value !== "team" && value !== "service") {
    throw new Error(`${field} is invalid.`);
  }
  return value;
}

function csv(form: FormData, field: string): string[] {
  return Array.from(
    new Set(
      String(form.get(field) ?? "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}
