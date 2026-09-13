import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  TemplateClient,
  type CanonicalTemplate,
  type TemplateInstantiation,
  type TemplatePreview,
} from "../../api/templates";
import { useRouter } from "../../app/router";
import { Card, CanonicalId, ErrorState, LoadingState, StatusBadge } from "../../components/States";
import {
  Detail,
  InstanceTable,
  Metric,
  PreviewReport,
  RequirementList,
  formatTemplateDate,
} from "./TemplatePresentation";
import { parseTemplateContent } from "./templateCreation";

const INSTANCE_LIMIT = 200;

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
      setInstances(loadedInstances.items.filter((item) => item.source.template_id === templateId));
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
    await mutate(() => client.revise(template.id, template.current_revision, parseTemplateContent(contentJson)));
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
    ) return;
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
        <RequirementList label="Secret-reference placeholders" values={requirements.secret_reference_placeholders} />
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
        {revision.state === "draft" ? <small>Drafts can be previewed, but must be published before they can be applied.</small> : null}
        {revision.content.provenance.trust === "untrusted" ? <small>Untrusted revisions may be previewed but must be activated before apply.</small> : null}
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
                  <td>{formatTemplateDate(item.created_at)}</td>
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
