import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ControlPlaneClient } from "../api/client";
import type {
  CanonicalProject,
  CanonicalWorkspaceIdentity,
  OwnerType,
  Page,
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

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical scope</p>
        <h1>Projects & workspaces</h1>
        <p>Project identity and the currently exposed northbound Workspace identity surface.</p>
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
        <Metric label="Identity-only workspaces" value={workspaces?.items.filter((item) => item.lifecycle === "identity_only").length ?? "—"} />
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

  const createWorkspace = async () => {
    if (!project) return;
    setCreating(true);
    try {
      const workspace = await client.createWorkspace({ project_id: project.id });
      navigate(`/workspaces/${workspace.id}`);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setCreating(false);
    }
  };

  if (error && !project) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!project) return <LoadingState />;
  return (
    <div className="stack">
      <header className="page-header detail-header">
        <div><p className="eyebrow">Project</p><h1>{project.name}</h1><CanonicalId value={project.id} /></div>
        <StatusBadge value={project.owner.type} />
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <div className="actions">
        <button className="primary" disabled={creating} onClick={() => void createWorkspace()}>{creating ? "Creating…" : "Create workspace"}</button>
        <button disabled={creating} onClick={() => void load()}>Refresh</button>
      </div>
      <div className="grid-two">
        <Card title="Project details">
          <DefinitionList values={{ Owner: `${project.owner.type}:${project.owner.id}`, Created: formatDate(project.created_at), Updated: formatDate(project.updated_at) }} />
        </Card>
        <Card title="Workspace summary">
          <DefinitionList values={{ Workspaces: workspaces.length, "Identity only": workspaces.filter((item) => item.lifecycle === "identity_only").length }} />
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
        <div><p className="eyebrow">Workspace</p><h1>Workspace identity</h1><CanonicalId value={workspace.id} /></div>
        <StatusBadge value={workspace.lifecycle} />
      </header>
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      {workspace.lifecycle === "identity_only" ? (
        <DegradedState
          title="Workspace lifecycle not exposed northbound yet"
          detail="This Control Plane resource currently exposes Workspace identity only. Snapshot, materialization, cleanup and retention actions are intentionally not offered by the browser until their canonical API is registered."
        />
      ) : null}
      <Card title="Workspace details">
        <DefinitionList values={{ Project: workspace.project_id, Owner: `${workspace.owner.type}:${workspace.owner.id}`, Created: workspace.created_at ? formatDate(workspace.created_at) : "—", Lifecycle: workspace.lifecycle }} />
        <p><AppLink href={`/projects/${workspace.project_id}`}>Open parent project</AppLink></p>
      </Card>
      <div className="actions"><button onClick={() => void load()}>Refresh</button></div>
    </div>
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
    <div className="table-wrap"><table><thead><tr><th>Workspace</th><th>Project</th><th>Lifecycle</th><th>Created</th></tr></thead><tbody>
      {workspaces.map((workspace) => <tr key={workspace.id}><td><AppLink href={`/workspaces/${workspace.id}`}><CanonicalId value={workspace.id} /></AppLink></td><td><AppLink href={`/projects/${workspace.project_id}`}><CanonicalId value={workspace.project_id} /></AppLink></td><td><StatusBadge value={workspace.lifecycle} /></td><td>{workspace.created_at ? formatDate(workspace.created_at) : "—"}</td></tr>)}
    </tbody></table></div>
  );
}

function DefinitionList({ values }: { values: Record<string, string | number> }) {
  return <dl>{Object.entries(values).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
