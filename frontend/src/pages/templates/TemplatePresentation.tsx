import type { ReactNode } from "react";
import type {
  CanonicalTemplate,
  TemplateInstantiation,
  TemplatePreview,
  TemplateResourceRef,
} from "../../api/templates";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState, StatusBadge } from "../../components/States";

export function TemplateTable({ templates }: { templates: CanonicalTemplate[] }) {
  if (!templates.length) {
    return <EmptyState title="No Templates yet" detail="Create one from canonical configuration or an existing resource." />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Template</th><th>Type</th><th>State</th><th>Revision</th><th>Updated</th></tr></thead>
        <tbody>
          {templates.map((template) => (
            <tr key={template.id}>
              <td>
                <AppLink href={`/templates/${encodeURIComponent(template.id)}`}>
                  {template.revision.content.name}
                </AppLink>
                <div><CanonicalId value={template.id} /></div>
              </td>
              <td>{template.revision.content.template_type}</td>
              <td><StatusBadge value={template.revision.state} /></td>
              <td>{template.current_revision}</td>
              <td>{formatTemplateDate(template.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PreviewReport({ preview }: { preview: TemplatePreview }) {
  const blockers = [
    ["Required capabilities", preview.missing_required_capability_ids],
    ["Required capability versions", preview.incompatible_capability_versions],
    ["Platform version", preview.incompatible_platform_versions],
    ["Missing contract versions", preview.missing_contract_versions],
    ["Contract versions", preview.incompatible_contract_versions],
    ["Plugins", preview.missing_plugin_ids],
    ["Connectors", preview.missing_connector_ids],
    ["Model policies", preview.missing_model_policy_refs],
    ["Permissions", preview.ungrantable_permissions],
    ["Workspace prerequisites", preview.missing_workspace_prerequisites],
    ["Placeholders", preview.unresolved_placeholders],
    ["Secret references", preview.unresolved_secret_reference_placeholders],
    ["Configuration references", preview.unvalidated_configuration_refs],
    ["Canonical handlers", preview.missing_handler_types],
  ] as const;
  return (
    <div className="stack">
      <p>
        <strong>Status:</strong> <StatusBadge value={preview.applicable ? "applicable" : "blocked"} />
        {" · "}Revision {preview.source.revision}
      </p>
      {preview.privileged_capability_ids.length ? (
        <div className="state state-warning" role="status">
          <strong>Privileged capabilities</strong>
          <p>{preview.privileged_capability_ids.join(", ")}</p>
        </div>
      ) : null}
      {blockers.map(([label, values]) => values.length ? (
        <div className="state state-warning" key={label}>
          <strong>Missing or incompatible: {label}</strong>
          <p>{values.join(", ")}</p>
        </div>
      ) : null)}
      {preview.missing_optional_capability_ids.length ? (
        <p><strong>Optional capabilities unavailable:</strong> {preview.missing_optional_capability_ids.join(", ")}</p>
      ) : null}
      {preview.incompatible_optional_capability_versions.length ? (
        <p>
          <strong>Optional capability versions incompatible:</strong>{" "}
          {preview.incompatible_optional_capability_versions.join(", ")}
        </p>
      ) : null}
      {preview.missing_optional_dependencies.length ? (
        <p><strong>Optional dependencies unavailable:</strong> {preview.missing_optional_dependencies.join(", ")}</p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Resource</th><th>Action</th><th>Description</th></tr></thead>
          <tbody>
            {preview.resource_changes.map((change, index) => (
              <tr key={`${change.resource_type}:${change.action}:${index}`}>
                <td>{change.resource_type}</td>
                <td>{change.action}</td>
                <td>{change.description ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function InstanceTable({ instances }: { instances: TemplateInstantiation[] }) {
  if (!instances.length) return <EmptyState title="No instances from this Template" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Instance</th><th>Revision</th><th>Resources</th><th>Created</th></tr></thead>
        <tbody>
          {instances.map((instance) => (
            <tr key={instance.id}>
              <td><CanonicalId value={instance.id} /></td>
              <td>{instance.source.revision}</td>
              <td>
                {instance.resource_refs.map((resource, index) => (
                  <span key={`${resource.resource_type}:${resource.resource_id}`}>
                    {index ? ", " : null}
                    <ResourceLink resource={resource} />
                  </span>
                ))}
              </td>
              <td>{formatTemplateDate(instance.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResourceLink({ resource }: { resource: TemplateResourceRef }) {
  const href = canonicalResourceHref(resource);
  if (!href) return <CanonicalId value={resource.resource_id} />;
  return <AppLink href={href}>{resource.resource_type}: <CanonicalId value={resource.resource_id} /></AppLink>;
}

export function canonicalResourceHref(resource: TemplateResourceRef): string | null {
  const id = encodeURIComponent(resource.resource_id);
  if (resource.resource_type === "agent") return `/agents/${id}`;
  if (resource.resource_type === "agent_team") return `/agent-teams/${id}`;
  if (resource.resource_type === "automation") return `/automations/${id}`;
  if (resource.resource_type === "project") return `/projects/${id}`;
  if (resource.resource_type === "workspace") return `/workspaces/${id}`;
  if (resource.resource_type === "workflow") return `/workflows/${id}`;
  if (resource.resource_type === "capability_assignment") return `/capability-assignments/${id}`;
  if (resource.resource_type === "model_routing_profile") return `/model-routing-profiles/${id}`;
  return null;
}

export function RequirementList({ label, values }: { label: string; values: string[] }) {
  return (
    <p>
      <strong>{label}:</strong> {values.length ? values.join(", ") : "—"}
    </p>
  );
}

export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}

export function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

export function formatTemplateDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
