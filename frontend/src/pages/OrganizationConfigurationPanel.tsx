import { useState, type FormEvent } from "react";
import type {
  CanonicalCollaborationTeam,
  CanonicalOrganization,
} from "../api/organizations";
import { OrganizationClient } from "../api/organizations";
import type { JsonValue } from "../api/types";
import { Card, ErrorState } from "../components/States";

interface OrganizationConfigurationPanelProps {
  client: OrganizationClient;
  organization: CanonicalOrganization;
  team: CanonicalCollaborationTeam | null;
  teams: CanonicalCollaborationTeam[];
  onChanged: () => Promise<void> | void;
}

export function OrganizationConfigurationPanel({
  client,
  organization,
  team,
  teams,
  onChanged,
}: OrganizationConfigurationPanelProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const run = async (key: string, operation: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await operation();
      await onChanged();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(null);
    }
  };

  const updateOrganization = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await run("organization.update", () =>
      client.updateOrganization(organization.id, {
        name: required(form, "name"),
        display_name: optionalNullable(form, "display_name"),
        administrator_actor_ids: csv(form, "administrator_actor_ids"),
        settings: jsonObject(form, "settings"),
        default_policy_refs: csv(form, "default_policy_refs"),
        default_configuration_refs: csv(form, "default_configuration_refs"),
      }),
    );
  };

  const configureTeam = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!team) return;
    const form = new FormData(event.currentTarget);
    await run("team.configure", () =>
      client.configureTeam(team.id, {
        name: required(form, "name"),
        description: String(form.get("description") ?? ""),
        parent_team_id: optionalNullable(form, "parent_team_id"),
        project_scope_refs: csv(form, "project_scope_refs"),
        default_policy_refs: csv(form, "default_policy_refs"),
        default_configuration_refs: csv(form, "default_configuration_refs"),
      }),
    );
  };

  return (
    <div className="stack">
      {error ? <ErrorState error={error} /> : null}
      <div className="two-column">
        <Card title="Edit organization">
          <form className="form-grid" key={organization.id} onSubmit={updateOrganization}>
            <label>
              Name
              <input name="name" defaultValue={organization.name} required />
            </label>
            <label>
              Display name
              <input name="display_name" defaultValue={organization.display_name ?? ""} />
            </label>
            <label>
              Administrator actor refs
              <input
                name="administrator_actor_ids"
                defaultValue={organization.administrator_actor_ids.join(", ")}
              />
            </label>
            <label>
              Default policy refs
              <input
                name="default_policy_refs"
                defaultValue={organization.default_policy_refs.join(", ")}
              />
            </label>
            <label>
              Default configuration refs
              <input
                name="default_configuration_refs"
                defaultValue={organization.default_configuration_refs.join(", ")}
              />
            </label>
            <label>
              Settings JSON
              <textarea
                name="settings"
                rows={5}
                defaultValue={JSON.stringify(organization.settings, null, 2)}
              />
            </label>
            <button
              className="primary"
              disabled={busy !== null || organization.status !== "active"}
            >
              {busy === "organization.update" ? "Saving…" : "Save organization"}
            </button>
          </form>
        </Card>

        <Card title="Configure selected team">
          {!team ? (
            <p>Select a team in the collaboration context to edit its canonical configuration.</p>
          ) : (
            <form className="form-grid" key={team.id} onSubmit={configureTeam}>
              <label>
                Name
                <input name="name" defaultValue={team.name} required />
              </label>
              <label>
                Description
                <input name="description" defaultValue={team.description} />
              </label>
              <label>
                Parent team
                <select name="parent_team_id" defaultValue={team.parent_team_id ?? ""}>
                  <option value="">None</option>
                  {teams
                    .filter((candidate) => candidate.id !== team.id)
                    .map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Project scope refs
                <input
                  name="project_scope_refs"
                  defaultValue={team.project_scope_refs.join(", ")}
                />
              </label>
              <label>
                Default policy refs
                <input
                  name="default_policy_refs"
                  defaultValue={team.default_policy_refs.join(", ")}
                />
              </label>
              <label>
                Default configuration refs
                <input
                  name="default_configuration_refs"
                  defaultValue={team.default_configuration_refs.join(", ")}
                />
              </label>
              <button className="primary" disabled={busy !== null || team.status !== "active"}>
                {busy === "team.configure" ? "Saving…" : "Save team"}
              </button>
            </form>
          )}
        </Card>
      </div>
    </div>
  );
}

function required(form: FormData, field: string): string {
  const value = String(form.get(field) ?? "").trim();
  if (!value) throw new Error(`${field} is required.`);
  return value;
}

function optionalNullable(form: FormData, field: string): string | null {
  const value = String(form.get(field) ?? "").trim();
  return value || null;
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

function jsonObject(form: FormData, field: string): Record<string, JsonValue> {
  const raw = String(form.get(field) ?? "{}").trim() || "{}";
  const value: unknown = JSON.parse(raw);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${field} must be a JSON object.`);
  }
  return value as Record<string, JsonValue>;
}
