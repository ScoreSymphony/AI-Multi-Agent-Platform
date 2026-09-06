import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ApprovalClient } from "../api/approvals";
import { AutomationClient } from "../api/automations";
import { BrowserSessionClient } from "../api/browserSession";
import { ControlPlaneClient } from "../api/client";
import { ControlPlaneCollectionClient } from "../api/collections";
import { ConversationClient } from "../api/conversations";
import { ComputeClient } from "../api/compute";
import { EvaluationClient } from "../api/evaluations";
import { IntegrationsClient } from "../api/integrations";
import { MemoryKnowledgeClient } from "../api/memoryKnowledge";
import { NotificationClient } from "../api/notifications";
import { OnboardingClient } from "../api/onboarding";
import { OrganizationClient } from "../api/organizations";
import { PluginsClient } from "../api/plugins";
import { RepositoryCollectionClient } from "../api/repositories";
import { TemplateClient } from "../api/templates";
import { VerificationClient } from "../api/verification";
import type { ReferenceCollection } from "../api/references";
import type { APImanifest } from "../api/types";
import { OnboardingCallout } from "../components/OnboardingCallout";
import { LoadingState } from "../components/States";
import { PermissionHintsProvider } from "../security/permissions";
import { approvalDecisionManifestState } from "./approvalManifest";
import { navigation } from "./navigation";
import { AppLink, matchPath, useRouter } from "./router";
import { templateManifestState } from "./templateManifest";
import {
  AgentDetailPage,
  AgentsPage,
  AgentTeamDetailPage,
  AgentTeamsPage,
} from "../pages/AgentsPage";
import { ApprovalDetailPage, ApprovalsPage } from "../pages/ApprovalsPage";
import { AutomationDetailPage, AutomationsPage } from "../pages/AutomationsPage";
import {
  CapabilitiesPage,
  CapabilityDetailPage,
  CapabilityProviderDetailPage,
} from "../pages/CapabilitiesPage";
import { CanonicalConfigurationDetailPage } from "../pages/CanonicalConfigurationDetailPage";
import { ChatPage } from "../pages/ChatPage";
import {
  ComputeNodeDetailPage,
  ComputePage,
  ComputeWorkerDetailPage,
  ComputeWorkerJobDetailPage,
} from "../pages/ComputePage";
import {
  EvaluationRunDetailPage,
  EvaluationSuiteDetailPage,
  EvaluationsPage,
} from "../pages/EvaluationsPage";
import {
  ConnectionDetailPage,
  ConnectorDefinitionDetailPage,
  IntegrationsPage,
} from "../pages/IntegrationsPage";
import {
  KnowledgeDetailPage,
  KnowledgePage,
  MemoryDetailPage,
  MemoryPage,
} from "../pages/MemoryKnowledgePages";
import { ModelDetailPage, ModelProviderDetailPage } from "../pages/ModelPages";
import { ModelsPage } from "../pages/ModelInventoryPage";
import { NotificationsPage } from "../pages/NotificationsPage";
import { ObservabilityPage } from "../pages/ObservabilityPage";
import { OnboardingPage } from "../pages/OnboardingPage";
import { OrganizationsPage } from "../pages/OrganizationsPage";
import { OverviewPage, UnavailablePage } from "../pages/Pages";
import {
  PluginCandidateDetailPage,
  PluginDetailPage,
  PluginsPage,
} from "../pages/PluginsPage";
import { ProjectDetailPage, WorkspaceDetailPage } from "../pages/ProjectPages";
import { ProjectsPage } from "../pages/ProjectListPage";
import { RepositoriesPage, RepositoryDetailPage } from "../pages/RepositoriesPage";
import { ReferencesPage } from "../pages/ReferencePages";
import { RunsPage } from "../pages/RunListPage";
import { SearchPage } from "../pages/SearchPage";
import { SettingsPage } from "../pages/SettingsPage";
import { ManagedTasksPage, TaskManagementDetailPage } from "../pages/TaskManagementPages";
import { TemplateDetailPage, TemplatesPage } from "../pages/TemplatesPage";
import { TerminalPage } from "../pages/TerminalPage";
import { UsagePage } from "../pages/UsagePage";
import { VerificationDetailPage, VerificationPage } from "../pages/VerificationPage";
import {
  VerificationBoundReferenceDetailPage,
  VerificationBoundRunDetailPage,
  VerificationBoundTaskDetailPage,
} from "../pages/VerificationBoundPages";

export type ManifestState = "loading" | "ready" | "unavailable";
export type ManifestResourceState = "loading" | "available" | "unavailable";

const EVALUATION_RESOURCES = ["evaluation-suites", "evaluation-runs"] as const;
const COMPUTE_RESOURCES = ["nodes", "workers", "worker-jobs"] as const;
const INTEGRATION_RESOURCES = ["connector-definitions", "connections"] as const;
const KNOWLEDGE_RESOURCES = ["knowledge", "knowledge-results"] as const;

export function Shell() {
  const { path } = useRouter();
  const baseUrl = import.meta.env.VITE_CONTROL_PLANE_URL ?? "";
  const session = useMemo(() => new BrowserSessionClient({ baseUrl }), [baseUrl]);
  const client = useMemo(
    () => new ControlPlaneClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const onboardingClient = useMemo(
    () => new OnboardingClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const collections = useMemo(
    () => new ControlPlaneCollectionClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const approvalClient = useMemo(
    () => new ApprovalClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const repositoryClient = useMemo(
    () => new RepositoryCollectionClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const conversationClient = useMemo(
    () => new ConversationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const automationClient = useMemo(
    () => new AutomationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const computeClient = useMemo(
    () => new ComputeClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const evaluationClient = useMemo(
    () => new EvaluationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const integrationsClient = useMemo(
    () => new IntegrationsClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const memoryKnowledgeClient = useMemo(
    () => new MemoryKnowledgeClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const notificationClient = useMemo(
    () => new NotificationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const organizationClient = useMemo(
    () => new OrganizationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const pluginsClient = useMemo(
    () => new PluginsClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const templateClient = useMemo(
    () => new TemplateClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const verificationClient = useMemo(
    () => new VerificationClient({ baseUrl, fetchImpl: session.fetch }),
    [baseUrl, session],
  );
  const [manifest, setManifest] = useState<APImanifest | null>(null);
  const [manifestState, setManifestState] = useState<ManifestState>("loading");
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    void client
      .manifest()
      .then((loadedManifest) => {
        setManifest(loadedManifest);
        setManifestState("ready");
      })
      .catch(() => {
        setManifest(null);
        setManifestState("unavailable");
      });
  }, [client]);
  useEffect(() => setMenuOpen(false), [path]);

  const projectMatch = matchPath("/projects/:projectId", path);
  const workspaceMatch = matchPath("/workspaces/:workspaceId", path);
  const repositoryMatch = matchPath("/repositories/:repositoryId", path);
  const taskManagementMatch = matchPath("/tasks/:taskId/manage", path);
  const taskMatch = matchPath("/tasks/:taskId", path);
  const runMatch = matchPath("/runs/:runId", path);
  const agentMatch = matchPath("/agents/:agentId", path);
  const agentTeamMatch = matchPath("/agent-teams/:teamId", path);
  const capabilityProviderMatch = matchPath("/tools/providers/:providerId", path);
  const capabilityMatch = matchPath("/tools/:capabilityId", path);
  const connectorDefinitionMatch = matchPath("/integrations/definitions/:definitionId", path);
  const connectionMatch = matchPath("/integrations/connections/:connectionId", path);
  const memoryMatch = matchPath("/memory/:memoryId", path);
  const knowledgeMatch = matchPath("/knowledge/:sourceId", path);
  const providerMatch = matchPath("/models/providers/:providerId", path);
  const modelMatch = matchPath("/models/:modelId", path);
  const evaluationSuiteMatch = matchPath("/evaluations/suites/:suiteRef", path);
  const evaluationRunMatch = matchPath("/evaluations/runs/:evaluationRunId", path);
  const computeNodeMatch = matchPath("/compute/nodes/:nodeId", path);
  const computeWorkerMatch = matchPath("/compute/workers/:workerId", path);
  const computeWorkerJobMatch = matchPath("/compute/jobs/:workerJobId", path);
  const pluginCandidateMatch = matchPath("/plugins/candidates/:pluginId", path);
  const pluginMatch = matchPath("/plugins/:pluginId", path);
  const automationMatch = matchPath("/automations/:automationId", path);
  const templateMatch = matchPath("/templates/:templateId", path);
  const workflowMatch = matchPath("/workflows/:workflowId", path);
  const capabilityAssignmentMatch = matchPath("/capability-assignments/:assignmentId", path);
  const modelRoutingProfileMatch = matchPath("/model-routing-profiles/:profileId", path);
  const approvalMatch = matchPath("/approvals/:approvalId", path);
  const verificationMatch = matchPath("/verification/:verificationId", path);
  const referenceMatch = referenceRoute(path);
  const navItem = navigation.find((item) => item.path === path);
  const pluginCandidatesAvailable = manifest?.resources.includes("plugin-candidates") ?? false;
  const approvalDecisionState = approvalDecisionManifestState(manifestState, manifest);
  const onboardingAvailable = manifest?.resources.includes("onboarding") ?? false;
  let content;
  if (path === "/") content = <OverviewPage client={client} />;
  else if (path === "/onboarding") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="First-run onboarding"
        resource="onboarding"
      >
        <OnboardingPage
          client={client}
          onboarding={onboardingClient}
          session={session}
          manifest={manifest}
        />
      </ManifestResourcePage>
    );
  }
  else if (path === "/chat") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Chat"
        resource="conversations"
      >
        <ChatPage client={conversationClient} />
      </ManifestResourcePage>
    );
  }
  else if (path === "/projects") content = <ProjectsPage client={client} />;
  else if (projectMatch) {
    content = <ProjectDetailPage client={client} projectId={projectMatch.projectId} />;
  } else if (workspaceMatch) {
    content = <WorkspaceDetailPage client={client} workspaceId={workspaceMatch.workspaceId} />;
  } else if (path === "/repositories") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Repositories"
        resource="repositories"
      >
        <RepositoriesPage client={repositoryClient} />
      </ManifestResourcePage>
    );
  } else if (repositoryMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Repositories"
        resource="repositories"
      >
        <RepositoryDetailPage
          client={repositoryClient}
          repositoryId={repositoryMatch.repositoryId}
        />
      </ManifestResourcePage>
    );
  } else if (path === "/tasks") content = <ManagedTasksPage client={client} />;
  else if (taskManagementMatch) {
    content = <TaskManagementDetailPage client={client} taskId={taskManagementMatch.taskId} />;
  } else if (taskMatch) {
    content = (
      <VerificationBoundTaskDetailPage
        client={client}
        verificationClient={verificationClient}
        taskId={taskMatch.taskId}
      />
    );
  }
  else if (path === "/runs") content = <RunsPage client={client} />;
  else if (runMatch) {
    content = (
      <VerificationBoundRunDetailPage
        client={client}
        verificationClient={verificationClient}
        runId={runMatch.runId}
      />
    );
  }
  else if (path === "/templates") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Templates"
        resource="templates"
      >
        <TemplatesPage client={templateClient} />
      </ManifestResourcePage>
    );
  } else if (templateMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Templates"
        resource="templates"
      >
        <TemplateDetailPage client={templateClient} templateId={templateMatch.templateId} />
      </ManifestResourcePage>
    );
  } else if (workflowMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Workflow"
        resource="workflows"
      >
        <CanonicalConfigurationDetailPage
          client={collections}
          collection="workflows"
          resourceId={workflowMatch.workflowId}
        />
      </ManifestResourcePage>
    );
  } else if (capabilityAssignmentMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Capability Assignment"
        resource="capability-assignments"
      >
        <CanonicalConfigurationDetailPage
          client={collections}
          collection="capability-assignments"
          resourceId={capabilityAssignmentMatch.assignmentId}
        />
      </ManifestResourcePage>
    );
  } else if (modelRoutingProfileMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Model Routing Profile"
        resource="model-routing-profiles"
      >
        <CanonicalConfigurationDetailPage
          client={collections}
          collection="model-routing-profiles"
          resourceId={modelRoutingProfileMatch.profileId}
        />
      </ManifestResourcePage>
    );
  }
  else if (path === "/agents") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Agents" resource="agents">
        <AgentsPage client={client} />
      </ManifestResourcePage>
    );
  } else if (agentMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Agents" resource="agents">
        <AgentDetailPage client={client} agentId={agentMatch.agentId} />
      </ManifestResourcePage>
    );
  } else if (path === "/agent-teams") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Agent Teams" resource="agent-teams">
        <AgentTeamsPage client={client} />
      </ManifestResourcePage>
    );
  } else if (agentTeamMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Agent Teams" resource="agent-teams">
        <AgentTeamDetailPage client={client} teamId={agentTeamMatch.teamId} />
      </ManifestResourcePage>
    );
  } else if (path === "/organizations") {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Organizations"
        resource="organizations"
      >
        <OrganizationsPage client={organizationClient} />
      </ManifestResourcePage>
    );
  } else if (path === "/files") content = <ReferencesPage client={client} />;
  else if (referenceMatch) {
    content = (
      <VerificationBoundReferenceDetailPage
        client={client}
        verificationClient={verificationClient}
        collection={referenceMatch.collection}
        resourceId={referenceMatch.resourceId}
      />
    );
  } else if (path === "/memory") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Memory" resource="memory">
        <MemoryPage client={memoryKnowledgeClient} />
      </ManifestResourcePage>
    );
  } else if (memoryMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Memory" resource="memory">
        <MemoryDetailPage client={memoryKnowledgeClient} memoryId={memoryMatch.memoryId} />
      </ManifestResourcePage>
    );
  } else if (path === "/knowledge") {
    content = (
      <ManifestResourcesPage
        state={manifestState}
        manifest={manifest}
        label="Knowledge"
        resources={KNOWLEDGE_RESOURCES}
      >
        <KnowledgePage client={memoryKnowledgeClient} />
      </ManifestResourcesPage>
    );
  } else if (knowledgeMatch) {
    content = (
      <ManifestResourcesPage
        state={manifestState}
        manifest={manifest}
        label="Knowledge"
        resources={KNOWLEDGE_RESOURCES}
      >
        <KnowledgeDetailPage client={memoryKnowledgeClient} sourceId={knowledgeMatch.sourceId} />
      </ManifestResourcesPage>
    );
  } else if (path === "/search") content = <SearchPage client={client} />;
  else if (path === "/tools") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Tools" resource="capabilities">
        <CapabilitiesPage client={client} />
      </ManifestResourcePage>
    );
  } else if (capabilityProviderMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Tools" resource="capability-providers">
        <CapabilityProviderDetailPage client={client} providerId={capabilityProviderMatch.providerId} />
      </ManifestResourcePage>
    );
  } else if (capabilityMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Tools" resource="capabilities">
        <CapabilityDetailPage client={client} capabilityId={capabilityMatch.capabilityId} />
      </ManifestResourcePage>
    );
  } else if (path === "/integrations") {
    content = (
      <ManifestResourcesPage
        state={manifestState}
        manifest={manifest}
        label="Integrations"
        resources={INTEGRATION_RESOURCES}
      >
        <IntegrationsPage client={integrationsClient} />
      </ManifestResourcesPage>
    );
  } else if (connectorDefinitionMatch) {
    content = (
      <ManifestResourcesPage
        state={manifestState}
        manifest={manifest}
        label="Integrations"
        resources={INTEGRATION_RESOURCES}
      >
        <ConnectorDefinitionDetailPage
          client={integrationsClient}
          definitionId={connectorDefinitionMatch.definitionId}
        />
      </ManifestResourcesPage>
    );
  } else if (connectionMatch) {
    content = (
      <ManifestResourcesPage
        state={manifestState}
        manifest={manifest}
        label="Integrations"
        resources={INTEGRATION_RESOURCES}
      >
        <ConnectionDetailPage
          client={integrationsClient}
          connectionId={connectionMatch.connectionId}
        />
      </ManifestResourcesPage>
    );
  } else if (path === "/models") content = <ModelsPage client={client} />;
  else if (providerMatch) {
    content = <ModelProviderDetailPage client={client} providerId={providerMatch.providerId} />;
  } else if (modelMatch) content = <ModelDetailPage client={client} modelId={modelMatch.modelId} />;
  else if (path === "/evaluations") {
    content = (
      <ManifestResourcesPage
        state={manifestState}
        manifest={manifest}
        label="Evaluations"
        resources={EVALUATION_RESOURCES}
      >
        <EvaluationsPage client={evaluationClient} />
      </ManifestResourcesPage>
    );
  } else if (evaluationSuiteMatch) {
    content = (
      <ManifestResourcesPage state={manifestState} manifest={manifest} label="Evaluations" resources={EVALUATION_RESOURCES}>
        <EvaluationSuiteDetailPage client={evaluationClient} suiteRef={evaluationSuiteMatch.suiteRef} />
      </ManifestResourcesPage>
    );
  } else if (evaluationRunMatch) {
    content = (
      <ManifestResourcesPage state={manifestState} manifest={manifest} label="Evaluations" resources={EVALUATION_RESOURCES}>
        <EvaluationRunDetailPage client={evaluationClient} evaluationRunId={evaluationRunMatch.evaluationRunId} />
      </ManifestResourcesPage>
    );
  } else if (path === "/compute") {
    content = (
      <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}>
        <ComputePage client={computeClient} />
      </ManifestResourcesPage>
    );
  } else if (computeNodeMatch) {
    content = (
      <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}>
        <ComputeNodeDetailPage client={computeClient} nodeId={computeNodeMatch.nodeId} />
      </ManifestResourcesPage>
    );
  } else if (computeWorkerMatch) {
    content = (
      <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}>
        <ComputeWorkerDetailPage client={computeClient} workerId={computeWorkerMatch.workerId} />
      </ManifestResourcesPage>
    );
  } else if (computeWorkerJobMatch) {
    content = (
      <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}>
        <ComputeWorkerJobDetailPage client={computeClient} workerJobId={computeWorkerJobMatch.workerJobId} />
      </ManifestResourcesPage>
    );
  } else if (path === "/plugins") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Plugins" resource="plugins">
        <PluginsPage client={pluginsClient} candidateAvailable={pluginCandidatesAvailable} />
      </ManifestResourcePage>
    );
  } else if (pluginCandidateMatch) {
    content = (
      <ManifestResourcePage
        state={manifestState}
        manifest={manifest}
        label="Plugin discovery"
        resource="plugin-candidates"
      >
        <PluginCandidateDetailPage client={pluginsClient} pluginId={pluginCandidateMatch.pluginId} />
      </ManifestResourcePage>
    );
  } else if (pluginMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Plugins" resource="plugins">
        <PluginDetailPage
          client={pluginsClient}
          pluginId={pluginMatch.pluginId}
          candidateAvailable={pluginCandidatesAvailable}
        />
      </ManifestResourcePage>
    );
  } else if (path === "/terminal") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Terminal" resource="terminal-sessions">
        <TerminalPage client={client} />
      </ManifestResourcePage>
    );
  } else if (path === "/automations") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Automations" resource="automations">
        <AutomationsPage collections={collections} automations={automationClient} />
      </ManifestResourcePage>
    );
  } else if (automationMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Automations" resource="automations">
        <AutomationDetailPage collections={collections} automations={automationClient} automationId={automationMatch.automationId} />
      </ManifestResourcePage>
    );
  } else if (path === "/verification") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Verification" resource="verifications">
        <VerificationPage client={verificationClient} />
      </ManifestResourcePage>
    );
  } else if (verificationMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Verification" resource="verifications">
        <VerificationDetailPage client={verificationClient} verificationId={verificationMatch.verificationId} />
      </ManifestResourcePage>
    );
  } else if (path === "/approvals") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Approvals" resource="approvals">
        <ApprovalsPage client={approvalClient} decisionState={approvalDecisionState} />
      </ManifestResourcePage>
    );
  } else if (approvalMatch) {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Approvals" resource="approvals">
        <ApprovalDetailPage
          client={approvalClient}
          approvalId={approvalMatch.approvalId}
          decisionState={approvalDecisionState}
        />
      </ManifestResourcePage>
    );
  } else if (path === "/notifications") {
    content = (
      <ManifestResourcePage state={manifestState} manifest={manifest} label="Notifications" resource="notifications">
        <NotificationsPage client={notificationClient} />
      </ManifestResourcePage>
    );
  } else if (path === "/events") content = <ObservabilityPage client={client} view="events" />;
  else if (path === "/observability") content = <ObservabilityPage client={client} view="observability" />;
  else if (path === "/usage") content = <UsagePage client={client} manifest={manifest} />;
  else if (path === "/settings") content = <SettingsPage session={session} />;
  else if (navItem) content = <UnavailablePage item={navItem} manifest={manifest} />;
  else content = <UnavailablePage item={{ label: "Unknown route" }} manifest={manifest} />;

  const groups = Array.from(new Set(navigation.map((item) => item.group)));
  const apiReady = manifestState === "ready" && manifest !== null;
  return (
    <PermissionHintsProvider>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="app-shell">
        <aside id="platform-navigation" className={menuOpen ? "sidebar sidebar-open" : "sidebar"}>
          <div className="brand"><span className="brand-mark">A</span><div><strong>Agent Platform</strong><small>Control Plane UI</small></div></div>
          <nav aria-label="Platform navigation">
            {groups.map((group) => (
              <div className="nav-group" key={group}>
                <span>{group}</span>
                {navigation.filter((item) => item.group === group).map((item) => {
                  const active = item.path === path;
                  return (
                    <AppLink
                      aria-current={active ? "page" : undefined}
                      className={active ? "active" : undefined}
                      href={item.path}
                      key={item.path}
                    >
                      {item.label}
                    </AppLink>
                  );
                })}
              </div>
            ))}
          </nav>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <button
              className="menu-button"
              aria-controls="platform-navigation"
              aria-expanded={menuOpen}
              aria-label="Toggle navigation"
              onClick={() => setMenuOpen((value) => !value)}
            >
              Menu
            </button>
            <div className="api-indicator" role="status" aria-live="polite">
              <span className={apiReady ? "dot dot-ready" : "dot"} />
              {apiStatusLabel(manifestState, manifest)}
            </div>
          </header>
          <main id="main" tabIndex={-1}>
            {path !== "/onboarding" && onboardingAvailable ? (
              <OnboardingCallout client={onboardingClient} />
            ) : null}
            {content}
          </main>
        </div>
      </div>
    </PermissionHintsProvider>
  );
}

function ManifestResourcePage({
  state,
  manifest,
  label,
  resource,
  children,
}: {
  state: ManifestState;
  manifest: APImanifest | null;
  label: string;
  resource: string;
  children: ReactNode;
}) {
  const resourceState = resource === "templates"
    ? templateManifestState(state, manifest)
    : manifestResourceState(state, manifest, resource);
  if (resourceState === "loading") return <LoadingState label={`Checking ${label} availability…`} />;
  if (resourceState === "unavailable") {
    return <UnavailablePage item={{ label, apiResource: resource }} manifest={manifest} />;
  }
  return children;
}

function ManifestResourcesPage({
  state,
  manifest,
  label,
  resources,
  children,
}: {
  state: ManifestState;
  manifest: APImanifest | null;
  label: string;
  resources: readonly string[];
  children: ReactNode;
}) {
  const resourceState = manifestResourcesState(state, manifest, resources);
  if (resourceState === "loading") return <LoadingState label={`Checking ${label} availability…`} />;
  if (resourceState === "unavailable") {
    const missingResource = resources.find((resource) => !manifest?.resources.includes(resource));
    return (
      <UnavailablePage
        item={{ label, apiResource: missingResource ?? resources[0] }}
        manifest={manifest}
      />
    );
  }
  return children;
}

export function manifestResourceState(
  state: ManifestState,
  manifest: APImanifest | null,
  resource: string,
): ManifestResourceState {
  if (state === "loading") return "loading";
  if (state !== "ready" || manifest === null) return "unavailable";
  return manifest.resources.includes(resource) ? "available" : "unavailable";
}

export function manifestResourcesState(
  state: ManifestState,
  manifest: APImanifest | null,
  resources: readonly string[],
): ManifestResourceState {
  if (state === "loading") return "loading";
  if (state !== "ready" || manifest === null) return "unavailable";
  return resources.every((resource) => manifest.resources.includes(resource)) ? "available" : "unavailable";
}

export function apiStatusLabel(state: ManifestState, manifest: APImanifest | null): string {
  if (state === "ready" && manifest !== null) return `/api/${manifest.api_version}`;
  if (state === "unavailable") return "API unavailable";
  return "Checking API";
}

function referenceRoute(path: string): { collection: ReferenceCollection; resourceId: string } | null {
  for (const collection of ["artifacts", "results", "plans", "steps"] as const) {
    const match = matchPath(`/${collection}/:resourceId`, path);
    if (match) return { collection, resourceId: match.resourceId };
  }
  return null;
}
