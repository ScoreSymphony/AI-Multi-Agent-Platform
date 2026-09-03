import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ControlPlaneClient } from "../api/client";
import type {
  CanonicalProject,
  CanonicalWorkspace,
  CanonicalWorkspaceIdentity,
  OwnerType,
  Page,
} from "../api/types";
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

const PROJECT_QUERY_KEY = "projects:updated_at:desc";
const WORKSPACE_QUERY_KEY = "workspaces:created_at:desc";

export function ProjectsPage({ client }: { client: ControlPlaneClient }) {
  const [projects, setProjects] = useState<Page<CanonicalProject> | null>(null);
  const [workspaces, setWorkspaces] = useState<Page<CanonicalWorkspaceIdentity> | null>(null);
  const [projectError, setProjectError] = useState<unknown>(null);
  const [workspaceError, setWorkspaceError] = useState<unknown>(null);
  const [createError, setCreateError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const projectPagination = useCursorPagination(PROJECT_QUERY_KEY);
  const workspacePagination = useCursorPagination(WORKSPACE_QUERY_KEY);
  const { navigate } = useRouter();

  const loadProjects = useCallback(async () => {
    try {
      setProjects(
        await client.listProjects({
          limit: 100,
          cursor: projectPagination.cursor,
          sort: "updated_at",
          direction: "desc",
        }),
      );
      setProjectError(null);
    } catch (nextError) {
      setProjectError(nextError);
    }
  }, [client, projectPagination.cursor]);

  const loadWorkspaces = useCallback(async () => {
    try {
      setWorkspaces(
        await client.listWorkspaces({
          limit: 100,
          cursor: workspacePagination.cursor,
          sort: "created_at",
          direction: "desc",
        }),
      );
      setWorkspaceError(null);
    } catch (nextError) {
      setWorkspaceError(nextError);
    }
  }, [client, workspacePagination.cursor]);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    void loadWorkspaces();
  }, [loadWorkspaces]);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    const form = new FormData(event.currentTarget);
    try {
      const project = await client.createProject({
        name: String(form.get("name") ?? ""),
        owner_type: String(form.get("owner_type") ?? "user") as OwnerType,
        owner_id: String(form.get("owner_id") ?? ""),
      });
      navigate(`/projects/${project.id}`);
    } catch (nextError) {
      setCreateError(nextError);
    } finally {
      setCreating(false);
    }
  };

  const canonicalOnPage = workspaces?.items.filter(isCanonicalWorkspace).length ?? "—";
  const identityOnlyOnPage =
    workspaces?.items.filter((item) => item.lifecycle === "identity_only").length ?? "—";

  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Canonical scope</p>
        <h1>Projects & workspaces</h1>
        <p>Project identity plus the deployment's canonical Workspace lifecycle surface.</p>
      </header>

      {createError ? <ErrorState error={createError} /> : null}
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
        <Metric label="Canonical on page" value={canonicalOnPage} />
        <Metric label="Identity fallback on page" value={identityOnlyOnPage} />
      </div>

      <Card title="Projects">
        {projectError ? <ErrorState error={projectError} onRetry={() => void loadProjects()} /> : null}
        {!projects ? <LoadingState /> : <ProjectTable projects={projects.items} />}
        {projects ? (
          <PaginationControls
            page={projects}
            pageNumber={projectPagination.pageNumber}
            hasPrevious={projectPagination.hasPrevious}
            onPrevious={projectPagination.previous}
            onRefresh={() => void loadProjects()}
            onNext={() => projectPagination.next(projects.next_cursor)}
          />
        ) : null}
      </Card>

      <Card title="Workspaces">
        {workspaceError ? <ErrorState error={workspaceError} onRetry={() => void loadWorkspaces()} /> : null}
        {!workspaces ? <LoadingState /> : <WorkspaceTable workspaces={workspaces.items} />}
        {workspaces ? (
          <PaginationControls
            page={workspaces}
            pageNumber={workspacePagination.pageNumber}
            hasPrevious={workspacePagination.hasPrevious}
            onPrevious={workspacePagination.previous}
            onRefresh={() => void loadWorkspaces()}
            onNext={() => workspacePagination.next(workspaces.next_cursor)}
          />
        ) : null}
      </Card>
    </div>
  );
}

function ProjectTable({ projects }: { projects: CanonicalProject[] }) {
  if (!projects.length) return <EmptyState title="No projects" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Project</th><th>Owner</th><th>Updated</th></tr></thead>
        <tbody>{projects.map((project) => (
          <tr key={project.id}>
            <td><AppLink href={`/projects/${project.id}`}>{project.name}</AppLink><div><CanonicalId value={project.id} /></div></td>
            <td>{project.owner.type}:{project.owner.id}</td>
            <td>{formatDate(project.updated_at)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function WorkspaceTable({ workspaces }: { workspaces: CanonicalWorkspaceIdentity[] }) {
  if (!workspaces.length) return <EmptyState title="No workspaces" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Workspace</th><th>Project</th><th>Lifecycle</th><th>Status / type</th><th>Created</th></tr></thead>
        <tbody>{workspaces.map((workspace) => (
          <tr key={workspace.id}>
            <td><AppLink href={`/workspaces/${workspace.id}`}><CanonicalId value={workspace.id} /></AppLink></td>
            <td><AppLink href={`/projects/${workspace.project_id}`}><CanonicalId value={workspace.project_id} /></AppLink></td>
            <td><StatusBadge value={workspace.lifecycle} /></td>
            <td>{isCanonicalWorkspace(workspace) ? `${workspace.status} · ${workspace.workspace_type}` : "identity only"}</td>
            <td>{workspace.created_at ? formatDate(workspace.created_at) : "—"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function isCanonicalWorkspace(workspace: CanonicalWorkspaceIdentity): workspace is CanonicalWorkspace {
  return workspace.lifecycle === "canonical";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}