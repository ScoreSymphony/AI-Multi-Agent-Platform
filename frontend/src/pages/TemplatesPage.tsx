import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  TemplateClient,
  emptyTemplateContent,
  type CanonicalTemplate,
  type TemplateContent,
  type TemplateInstantiation,
  type TemplatePreview,
  type TemplateResourceRef,
} from "../api/templates";
import type { Page } from "../api/types";
import { useCursorPagination } from "../app/pagination";
import { AppLink, useRouter } from "../app/router";
import { PaginationControls } from "../components/Pagination";
import {
  CanonicalId,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";

const TEMPLATE_QUERY_KEY = "templates:updated";
const INSTANCE_LIMIT = 200;

type ExportKind =
  | "agent"
  | "agent_team"
  | "workflow"
  | "capability_assignment"
  | "model_routing_profile"
  | "automation"
  | "project"
  | "workspaces";

export function TemplatesPage({ client }: { client: TemplateClient }) {
  const { navigate } = useRouter();
  const [templates, setTemplates] = useState<Page<CanonicalTemplate> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [exportKind, setExportKind] = useState<ExportKind>("agent");
  const [sourceRef, setSourceRef] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [projectTemplateRef, setProjectTemplateRef] = useState("");
  const [projectTemplateRevision, setProjectTemplateRevision] = useState("");
  const [contentJson, setContentJson] = useState(() =>
    JSON.stringify(emptyTemplateContent(), null, 2),
  );
  const pagination = useCursorPagination(TEMPLATE_QUERY_KEY);

  const load = useCallback(async () => {
    try {
      setTemplates(await client.listTemplates({ limit: 50, cursor: pagination.cursor }));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, pagination.cursor]);

  useEffect(() => void load(), [load]);

  const createFromExisting = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setActionError(null);
    try {
      let created: CanonicalTemplate;
      const name = optionalText(templateName);
      if (exportKind === "agent") {
        created = await client.createFromAgent(sourceRef, { name });
      } else if (exportKind === "agent_team") {
        created = await client.createFromAgentTeam(sourceRef, { name });
      } else if (exportKind === "workflow") {
        created = await client.createFromWorkflow(sourceRef, { name });
      } else if (exportKind === "capability_assignment") {
        created = await client.createFromCapabilityAssignment(sourceRef, { name });
      } else if (exportKind === "model_routing_profile") {
        created = await client.createFromModelRoutingProfile(sourceRef, { name });
      } else if (exportKind === "automation") {
        created = await client.createFromAutomation(sourceRef, { name });
      } else if (exportKind === "project") {
        created = await client.createFromProject(sourceRef, { name });
      } else {
        const workspaceIds = sourceRef
          .split(/[\n,]/)
          .map((value) => value.trim())
          .filter(Boolean);
        created = await client.createFromWorkspaces(workspaceIds, {
          name: templateName.trim() || "Workspace structure",
          project_template_id: optionalText(projectTemplateRef),
          project_template_revision: optionalRevision(projectTemplateRevision),
        });
      }
      navigate(`/templates/${encodeURIComponent(created.id)}`);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const createFromJson = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setActionError(null);
    try {
      const created = await client.create(parseTemplateContent(contentJson));
      navigate(`/templates/${encodeURIComponent(created.id)}`);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Reusable configuration</p>
        <h1>Templates</h1>
        <p>
          Versioned configuration intent for canonical platform resources. Preview resolves
          dependencies, compatibility and privileges on the server before anything is created.
        </p>
      </header>

      {actionError ? <ErrorState error={actionError} /> : null}

      <Card title="Template library">
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {!templates ? <LoadingState label="Loading Templates…" /> : (
          <>
            <TemplateTable templates={templates.items} />
            <PaginationControls
              page={templates}
              pageNumber={pagination.pageNumber}
              hasPrevious={pagination.hasPrevious}
              onPrevious={pagination.previous}
              onRefresh={() => void load()}
              onNext={() => pagination.next(templates.next_cursor)}
            />
          </>
        )}
      </Card>

      <Card title="Create from an existing canonical resource">
        <form className="stack" onSubmit={(event) => void createFromExisting(event)}>
          <label>
            Source type
            <select
              value={exportKind}
              onChange={(event) => setExportKind(event.target.value as ExportKind)}
            >
              <option value="agent">Agent</option>
              <option value="agent_team">Agent Team</option>
              <option value="workflow">Workflow</option>
              <option value="capability_assignment">Capability Assignment</option>
              <option value="model_routing_profile">Model Routing Profile</option>
              <option value="automation">Automation</option>
              <option value="project">Project</option>
              <option value="workspaces">Workspace structure</option>
            </select>
          </label>
          <label>
            {exportKind === "workspaces" ? "Workspace IDs (comma or line separated)" : "Canonical source ID"}
            <textarea
              rows={exportKind === "workspaces" ? 3 : 1}
              required
              value={sourceRef}
              onChange={(event) => setSourceRef(event.target.value)}
            />
          </label>
          <label>
            Template name {exportKind === "workspaces" ? "" : "(optional)"}
            <input
              required={exportKind === "workspaces"}
              value={templateName}
              onChange={(event) => setTemplateName(event.target.value)}
            />
          </label>
          {exportKind === "workspaces" ? (
            <div className="detail-grid">
              <label>
                Project Template ID (optional)
                <input
                  value={projectTemplateRef}
                  onChange={(event) => setProjectTemplateRef(event.target.value)}
                />
              </label>
              <label>
                Project Template revision (optional)
                <input
                  inputMode="numeric"
                  value={projectTemplateRevision}
                  onChange={(event) => setProjectTemplateRevision(event.target.value)}
                />
              </label>
            </div>
          ) : null}
          <div className="button-row">
            <button disabled={busy} type="submit">Create draft</button>
          </div>
        </form>
      </Card>

      <Card title="Create from canonical Template JSON">
        <p>
          Advanced surface for composite or future Template types. Plaintext secrets and
          runtime-private fields are still rejected by the server.
        </p>
        <form className="stack" onSubmit={(event) => void createFromJson(event)}>
          <label>
            Template content
            <textarea
              rows={18}
              spellCheck={false}
              value={contentJson}
              onChange={(event) => setContentJson(event.target.value)}
            />
          </label>
          <div className="button-row">
            <button disabled={busy} type="submit">Create draft from JSON</button>
          </div>
        </form>
      </Card>
    </div>
  );
}

export function TemplateDetailPage({
  client,
  templateId,
}: {
  client: TemplateClient;
  templateId: string;
}) {
  const { navigate } = useRouter();
  const [template, setTemplate] = useState<CanonicalTemplate | null>(null);
  const [instances, setInstances] = useState<TemplateInstantiation[]>([]);
  const [preview, setPreview] = useState<TemplatePreview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [contentJson, setContentJson] = useState("");

  const load = useCallback(async () => {
    try {
      const [loadedTemplate, loadedInstances] = await Promise.all([
        client.getTemplate(templateId),
        client.listInstances({ limit: INSTANCE_LIMIT }),
      ]);
      setTemplate(loadedTemplate);
      setContentJson(JSON.stringify(loadedTemplate.revision.content, null, 2));
      setInstances(
        loadedInstances.items.filter((item) => item.source.template_id === templateId),
      );
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, templateId]);

  useEffect(() => void load(), [load]);

  const runPreview = async () => {
    if (!template) return;
    setBusy(true);
    setActionError(null);
    try {
      setPreview(
        await client.preview(template.id, {
          revision: template.revision.revision,
          allow_draft: template.revision.state === "draft",
        }),
      );
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!template) return;
    await mutate(() => client.publish(template.id, template.current_revision));
  };

  const activateUntrusted = async () => {
    if (!template) return;
    await mutate(() => client.activateUntrusted(template.id, template.current_revision));
  };

  const revise = async (event: FormEvent) => {
    event.preventDefault();
    if (!template) return;
    await mutate(() =>
      client.revise(template.id, template.current_revision, parseTemplateContent(contentJson)),
    );
  };

  const cloneOrFork = async (mode: "clone" | "fork") => {
    if (!template) return;
    setBusy(true);
    setActionError(null);
    try {
      const created = mode === "clone"
        ? await client.clone(template.id, { revision: template.revision.revision })
        : await client.fork(template.id, { revision: template.revision.revision });
      navigate(`/templates/${encodeURIComponent(created.id)}`);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (
      !template
      || template.revision.state !== "published"
      || template.revision.content.provenance.trust === "untrusted"
      || !preview?.applicable
      || preview.source.revision !== template.revision.revision
    ) {
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await client.apply(template.id, preview.source.revision);
      setPreview(null);
      await load();
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const mutate = async (action: () => Promise<CanonicalTemplate>) => {
    setBusy(true);
    setActionError(null);
    try {
      const updated = await action();
      setTemplate(updated);
      setContentJson(JSON.stringify(updated.revision.content, null, 2));
      setPreview(null);
    } catch (nextError) {
      setActionError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!template) return <LoadingState label="Loading Template…" />;

  const revision = template.revision;
  const requirements = revision.content.requirements;
  const canApplyPreview =
    revision.state === "published"
    && revision.content.provenance.trust !== "untrusted"
    && preview?.applicable === true
    && preview.source.revision === revision.revision;

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Templates / {revision.content.template_type}</p>
        <h1>{revision.content.name}</h1>
        <p>{revision.content.description}</p>
        <p><CanonicalId value={template.id} /></p>
      </header>

      {actionError ? <ErrorState error={actionError} /> : null}

      <div className="metrics">
        <Metric label="Current revision" value={template.current_revision} />
        <Metric label="Published revision" value={template.latest_published_revision ?? "—"} />
        <Metric label="State" value={revision.state} />
        <Metric label="Trust" value={revision.content.provenance.trust} />
      </div>

      <Card title="Revision and provenance">
        <dl className="detail-grid">
          <Detail label="State"><StatusBadge value={revision.state} /></Detail>
          <Detail label="Type">{revision.content.template_type}</Detail>
          <Detail label="Owner">{template.owner_ref.type}: {template.owner_ref.id}</Detail>
          <Detail label="Project scope">{template.project_id ?? "—"}</Detail>
          <Detail label="Organization scope">{template.organization_id ?? "—"}</Detail>
          <Detail label="Author">{revision.content.provenance.author}</Detail>
          <Detail label="Source">{revision.content.provenance.source}</Detail>
          <Detail label="Trust"><StatusBadge value={revision.content.provenance.trust} /></Detail>
          <Detail label="Provider agnostic">{revision.content.compatibility.provider_agnostic ? "yes" : "no"}</Detail>
          <Detail label="Orchestrator agnostic">{revision.content.compatibility.orchestrator_agnostic ? "yes" : "no"}</Detail>
        </dl>
        {revision.state === "published" && revision.content.provenance.trust === "untrusted" ? (
          <div className="state state-warning" role="status">
            <strong>Explicit activation required</strong>
            <p>
              This published revision is untrusted. Review it, then activate it to create a new
              trusted revision before normal apply is enabled.
            </p>
          </div>
        ) : null}
        <div className="button-row">
          {revision.state === "draft" ? (
            <button disabled={busy} onClick={() => void publish()}>Publish revision</button>
          ) : null}
          {revision.state === "published" && revision.content.provenance.trust === "untrusted" ? (
            <button disabled={busy} onClick={() => void activateUntrusted()}>
              Validate and activate revision
            </button>
          ) : null}
          <button disabled={busy} onClick={() => void cloneOrFork("clone")}>Clone</button>
          <button disabled={busy} onClick={() => void cloneOrFork("fork")}>Fork</button>
        </div>
      </Card>

      <Card title="Dependencies and requirements">
        <RequirementList
          label="Dependencies"
          values={revision.content.dependencies.map(
            (item) => `${item.template_id}@${item.revision ?? "latest"}${item.optional ? " (optional)" : ""}`,
          )}
        />
        <RequirementList
          label="Capabilities"
          values={requirements.capabilities.map(
            (item) => `${item.capability_id}${item.optional ? " (optional)" : ""}${item.privileged ? " (privileged)" : ""}`,
          )}
        />
        <RequirementList label="Plugins" values={requirements.plugin_ids} />
        <RequirementList label="Connectors" values={requirements.connector_ids} />
        <RequirementList label="Model policies" values={requirements.model_policy_refs} />
        <RequirementList label="Permissions" values={requirements.permission_actions} />
        <RequirementList label="Workspaces" values={requirements.workspace_prerequisites} />
        <RequirementList label="Placeholders" values={requirements.placeholders} />
        <RequirementList
          label="Secret-reference placeholders"
          values={requirements.secret_reference_placeholders}
        />
      </Card>

      <Card title="Preview and apply">
        <p>
          Compatibility data is resolved by the server. The browser cannot supply capability,
          permission, plugin, connector, model-policy or Workspace availability claims.
        </p>
        <div className="button-row">
          <button disabled={busy} onClick={() => void runPreview()}>Preview current revision</button>
          <button disabled={busy || !canApplyPreview} onClick={() => void apply()}>
            Apply previewed revision
          </button>
        </div>
        {revision.state === "draft" ? (
          <small>Drafts can be previewed, but must be published before they can be applied.</small>
        ) : null}
        {revision.content.provenance.trust === "untrusted" ? (
          <small>Untrusted revisions may be previewed but must be activated before apply.</small>
        ) : null}
        {preview ? <PreviewReport preview={preview} /> : null}
      </Card>

      <Card title="Edit as a new draft revision">
        <p>
          Saving appends a new draft revision to the same Template. A published current revision
          can be edited this way without changing the already-published revision or prior instances.
        </p>
        <form className="stack" onSubmit={(event) => void revise(event)}>
          <label>
            Canonical Template content
            <textarea
              rows={20}
              spellCheck={false}
              value={contentJson}
              onChange={(event) => setContentJson(event.target.value)}
            />
          </label>
          <div className="button-row">
            <button disabled={busy} type="submit">Save new draft revision</button>
          </div>
        </form>
      </Card>

      <Card title="Instances created from this Template">
        <InstanceTable instances={instances} />
      </Card>

      <Card title="Revision history">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Revision</th><th>State</th><th>Created</th><th>Source</th></tr></thead>
            <tbody>
              {template.revisions.map((item) => (
                <tr key={item.revision}>
                  <td>{item.revision}</td>
                  <td><StatusBadge value={item.state} /></td>
                  <td>{formatDate(item.created_at)}</td>
                  <td>{item.content.provenance.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function TemplateTable({ templates }: { templates: CanonicalTemplate[] }) {
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
              <td>{formatDate(template.updated_at)}</td>
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

function InstanceTable({ instances }: { instances: TemplateInstantiation[] }) {
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
              <td>{formatDate(instance.created_at)}</td>
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

function canonicalResourceHref(resource: TemplateResourceRef): string | null {
  const id = encodeURIComponent(resource.resource_id);
  if (resource.resource_type === "agent") return `/agents/${id}`;
  if (resource.resource_type === "agent_team") return `/agent-teams/${id}`;
  if (resource.resource_type === "automation") return `/automations/${id}`;
  if (resource.resource_type === "project") return `/projects/${id}`;
  if (resource.resource_type === "workspace") return `/workspaces/${id}`;
  return null;
}

function RequirementList({ label, values }: { label: string; values: string[] }) {
  return (
    <p>
      <strong>{label}:</strong> {values.length ? values.join(", ") : "—"}
    </p>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function parseTemplateContent(value: string): TemplateContent {
  const parsed: unknown = JSON.parse(value);
  if (!isRecord(parsed) || typeof parsed.name !== "string" || typeof parsed.template_type !== "string") {
    throw new Error("Template content must be a canonical JSON object with name and template_type");
  }
  return parsed as unknown as TemplateContent;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

function optionalRevision(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const revision = Number(trimmed);
  if (!Number.isInteger(revision) || revision < 1) {
    throw new Error("Project Template revision must be a positive integer");
  }
  return revision;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
