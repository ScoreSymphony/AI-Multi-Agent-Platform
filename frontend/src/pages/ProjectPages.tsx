import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ControlPlaneClient } from "../api/client";
import type {
  CanonicalProject,
  CanonicalWorkspace,
  CanonicalWorkspaceIdentity,
  OwnerType,
  Page,
  WorkspaceType,
} from "../api/types";
import { AppLink, useRouter } from "../app/router";
import {
  CanonicalId,
  Card,
  DegradedState,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import { isCanonicalId } from "../platform/id";

const workspaceTypes: WorkspaceType[] = [
  "persistent_project",
  "ephemeral_task",
  "isolated_run",
  "read_only_source",
  "cloned",
  "remote",
];

export function ProjectsPage({ client }: { client: ControlPlaneClient }) {
  const [projects, setProjects] = useState<Page<CanonicalProject> | null>(null);
  const [workspaces, setWorkspaces] = useState<Page<CanonicalWorkspaceIdentity> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const { navigate } = useRouter();

  const load = useCallback(async () => {
    try {
      const [nextProjects, nextWorkspaces] = await Promise.all([
        client.listProjects({ limit: 100, sort: "updated_at", direction: "desc" }),
        client.listWorkspaces({ limit: 100, sort: "created_at", direction: "desc" }),
      ]);
      setProjects(nextProjects);
      setWorkspaces(nextWorkspaces);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      const project = await client.createProject({
        name: String(form.get("name") ?? ""),
        owner_type: String(form.get("owner_type") ?? "user") as OwnerType,
        owner_id: String(form.get("owner_id") ?? ""),
      });
      navigate(`/projects/${project.id}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setCreating(false);
    }
  };

  const canonicalWorkspaces = workspaces?.items.filter(isCanonicalWorkspace).length ?? "—";
  const identityOnlyWorkspaces = workspaces?.items.filter((item) => item.lifecycle === "identity_only").length ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical scope</p>
        <h1>Projects & workspaces</h1>
        <p>Project identity plus the deployment's canonical Workspace lifecycle surface.</p>
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title="Create project">
        <form className="form-grid" onSubmit={create}>
          <label>Name<input name="name" required /></label>
          <label>Owner type<select name="owner_type" defaultValue="user"><option value="user">user</option><option value="organization">organization</option><option value="team">team</option><option value="service">service</option></select></label>
          <label>Owner ID<input name="owner_id" defaultValue="local" required /></label>
          <button className="primary" disabled={creating}>{creating ? "Creating…" : "Create project"}</button>
        </form>
      </Card>
      <div className="metrics">
        <Metric label="Projects" value={projects?.total ?? "—"} />
        <Metric label="Workspaces" value={workspaces?.total ?? "—"} />
        <Metric label="Canonical lifecycle" value={canonicalWorkspaces} />
        <Metric label="Identity fallback" value={identityOnlyWorkspaces} />
      </div>
      <Card title="Projects">
        {!projects ? <LoadingState /> : <ProjectTable projects={projects.items} workspaces={workspaces?.items ?? []} />}
      </Card>
      <Card title="Workspaces">
        {!workspaces ? <LoadingState /> : <WorkspaceTable workspaces={workspaces.items} />}
      </Card>
      <div className="actions"><button onClick={() => void load()}>Refresh</button></div>
    </div>
  );
}

export function ProjectDetailPage({ client, projectId }: { client: ControlPlaneClient; projectId: string }) {
  const [project, setProject] = useState<CanonicalProject | null>(null);
  const [workspaces, setWorkspaces] = useState<CanonicalWorkspaceIdentity[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const { navigate } = useRouter();

  const load = useCallback(async () => {
    if (!isCanonicalId(projectId)) {
      setError(new Error("This route does not contain a valid canonical Project ID."));
      return;
    }
    try {
      const [nextProject, nextWorkspaces] = await Promise.all([
        client.getProject(projectId),
        client.listWorkspaces({ limit: 100, filters: { project_id: projectId }, sort: "created_at", direction: "desc" }),
      ]);
      setProject(nextProject);
      setWorkspaces(nextWorkspaces.items);
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!project) return;
    setCreating(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const workspaceType = String(form.get("workspace_type") ?? "persistent_project") as WorkspaceType;
    const accessMode = workspaceType === "read_only_source" ? "read_only" : "read_write";
    const retention = workspaceType === "ephemeral_task" || workspaceType === "isolated_run" ? "ephemeral" : "persistent";
    try {
      const workspace = await client.createWorkspace({
        project_id: project.id,
        workspace_type: workspaceType,
        access_mode: accessMode,
        retention,
      });
      navigate(`/workspaces/${workspace.id}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setCreating(false);
    }
  };

  if (error && !project) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!project) return <LoadingState />;
  const canonicalCount = workspaces.filter(isCanonicalWorkspace).length;
  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div><p className="eyebrow">Project</p><h1>{project.name}</h1><CanonicalId value={project.id} /></div>
        <StatusBadge value={project.owner.type} />
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <Card title="Create workspace">
        <form className="filter-row" onSubmit={createWorkspace}>
          <label>Workspace type
            <select name="workspace_type" defaultValue="persistent_project">
              {workspaceTypes.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <button className="primary" disabled={creating}>{creating ? "Creating…" : "Create workspace"}</button>
        </form>
        <p className="muted">Access mode and retention follow the canonical #37 defaults for the selected type. Time-bounded retention is not offered until the northbound create contract accepts an expiry timestamp.</p>
      </Card>
      <div className="actions"><button disabled={creating} onClick={() => void load()}>Refresh</button></div>
      <div className="grid-two">
        <Card title="Project details">
          <DefinitionList values={{ Owner: `${project.owner.type}:${project.owner.id}`, Created: formatDate(project.created_at), Updated: formatDate(project.updated_at) }} />
        </Card>
        <Card title="Workspace summary">
          <DefinitionList values={{ Workspaces: workspaces.length, Canonical: canonicalCount, "Identity fallback": workspaces.length - canonicalCount }} />
        </Card>
      </div>
      <Card title="Project workspaces"><WorkspaceTable workspaces={workspaces} /></Card>
    </div>
  );
}

export function WorkspaceDetailPage({ client, workspaceId }: { client: ControlPlaneClient; workspaceId: string }) {
  const [workspace, setWorkspace] = useState<CanonicalWorkspaceIdentity | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    if (!isCanonicalId(workspaceId)) {
      setError(new Error("This route does not contain a valid canonical Workspace ID."));
      return;
    }
    try {
      setWorkspace(await client.getWorkspace(workspaceId));
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !workspace) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!workspace) return <LoadingState />;
  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div><p className="eyebrow">Workspace</p><h1>{isCanonicalWorkspace(workspace) ? workspace.workspace_type : "Workspace identity"}</h1><CanonicalId value={workspace.id} /></div>
        <StatusBadge value={isCanonicalWorkspace(workspace) ? workspace.status : workspace.lifecycle} />
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {workspace.lifecycle === "identity_only" ? (
        <DegradedState
          title="Workspace provider not configured in this deployment"
          detail="The Control Plane is using its compatibility identity fallback. Canonical #37 lifecycle metadata becomes available when a WorkspaceProvider is composed behind the same /api/v1/workspaces contract."
        />
      ) : (
        <CanonicalWorkspaceDetails workspace={workspace} />
      )}
      <Card title="Workspace identity">
        <DefinitionList values={{
          Project: workspace.project_id,
          Owner: `${workspace.owner.type}:${workspace.owner.id}`,
          Created: workspace.created_at ? formatDate(workspace.created_at) : "—",
          Lifecycle: workspace.lifecycle,
        }} />
        <p><AppLink href={`/projects/${workspace.project_id}`}>Open parent project</AppLink></p>
      </Card>
      <div className="actions"><button onClick={() => void load()}>Refresh</button></div>
    </div>
  );
}

function CanonicalWorkspaceDetails({ workspace }: { workspace: CanonicalWorkspace }) {
  return (
    <>
      <div className="grid-two">
        <Card title="Lifecycle">
          <DefinitionList values={{
            Type: workspace.workspace_type,
            Status: workspace.status,
            Access: workspace.access_mode,
            Retention: workspace.retention,
            Revision: workspace.revision,
            "Base snapshot": workspace.base_snapshot_id ?? "—",
            "Last used": formatDate(workspace.last_used_at),
            Expires: workspace.expires_at ? formatDate(workspace.expires_at) : "—",
          }} />
        </Card>
        <Card title="Bindings">
          <DefinitionList values={{
            "Active tasks": workspace.active_task_ids.length,
            "Active runs": workspace.active_run_ids.length,
            Sources: workspace.source_refs.length,
            "Policy labels": workspace.policy_labels.length,
          }} />
        </Card>
      </div>
      <Card title="Active tasks & runs">
        {!workspace.active_task_ids.length && !workspace.active_run_ids.length ? <EmptyState title="No active bindings" /> : (
          <div className="grid-two">
            <ReferenceLinks title="Tasks" ids={workspace.active_task_ids} prefix="/tasks/" />
            <ReferenceLinks title="Runs" ids={workspace.active_run_ids} prefix="/runs/" />
          </div>
        )}
      </Card>
      <Card title="Sources">
        {!workspace.source_refs.length ? <EmptyState title="No workspace sources" /> : (
          <div className="table-wrap"><table><thead><tr><th>Kind</th><th>Reference</th><th>Revision</th><th>Checksum</th></tr></thead><tbody>
            {workspace.source_refs.map((source, index) => <tr key={`${source.kind}:${source.ref}:${index}`}><td>{source.kind}</td><td><code>{source.ref}</code></td><td>{source.revision ?? "—"}</td><td>{source.checksum ? compact(source.checksum) : "—"}</td></tr>)}
          </tbody></table></div>
        )}
      </Card>
    </>
  );
}

function ProjectTable({ projects, workspaces }: { projects: CanonicalProject[]; workspaces: CanonicalWorkspaceIdentity[] }) {
  if (!projects.length) return <EmptyState title="No projects" />;
  return (
    <div className="table-wrap"><table><thead><tr><th>Project</th><th>Owner</th><th>Workspaces</th><th>Updated</th></tr></thead><tbody>
      {projects.map((project) => <tr key={project.id}><td><AppLink href={`/projects/${project.id}`}>{project.name}</AppLink><div><CanonicalId value={project.id} /></div></td><td>{project.owner.type}:{project.owner.id}</td><td>{workspaces.filter((workspace) => workspace.project_id === project.id).length}</td><td>{formatDate(project.updated_at)}</td></tr>)}
    </tbody></table></div>
  );
}

function WorkspaceTable({ workspaces }: { workspaces: CanonicalWorkspaceIdentity[] }) {
  if (!workspaces.length) return <EmptyState title="No workspaces" />;
  return (
    <div className="table-wrap"><table><thead><tr><th>Workspace</th><th>Project</th><th>Lifecycle</th><th>Status / type</th><th>Created</th></tr></thead><tbody>
      {workspaces.map((workspace) => <tr key={workspace.id}><td><AppLink href={`/workspaces/${workspace.id}`}><CanonicalId value={workspace.id} /></AppLink></td><td><AppLink href={`/projects/${workspace.project_id}`}><CanonicalId value={workspace.project_id} /></AppLink></td><td><StatusBadge value={workspace.lifecycle} /></td><td>{isCanonicalWorkspace(workspace) ? `${workspace.status} · ${workspace.workspace_type}` : "identity only"}</td><td>{workspace.created_at ? formatDate(workspace.created_at) : "—"}</td></tr>)}
    </tbody></table></div>
  );
}

function ReferenceLinks({ title, ids, prefix }: { title: string; ids: string[]; prefix: string }) {
  return <div><strong>{title}</strong>{ids.length ? <ul className="reference-list">{ids.map((id) => <li key={id}><AppLink href={`${prefix}${id}`}><CanonicalId value={id} /></AppLink></li>)}</ul> : <p>—</p>}</div>;
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return <dl>{Object.entries(values).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
function isCanonicalWorkspace(workspace: CanonicalWorkspaceIdentity): workspace is CanonicalWorkspace {
  return workspace.lifecycle === "canonical";
}
function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
function compact(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 8)}…${value.slice(-8)}`;
}
