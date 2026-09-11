import type { ReactNode } from "react";
import type { ReferenceCollection } from "../../api/references";
import type { APImanifest } from "../../api/types";
import { AgentDetailPage, AgentsPage, AgentTeamDetailPage, AgentTeamsPage } from "../../pages/AgentsPage";
import { ApprovalDetailPage, ApprovalsPage } from "../../pages/ApprovalsPage";
import { AutomationDetailPage, AutomationsPage } from "../../pages/AutomationsPage";
import { CapabilitiesPage, CapabilityDetailPage, CapabilityProviderDetailPage } from "../../pages/CapabilitiesPage";
import { CanonicalConfigurationDetailPage } from "../../pages/CanonicalConfigurationDetailPage";
import { ChatPage } from "../../pages/ChatPage";
import { ComputeNodeDetailPage, ComputePage, ComputeWorkerDetailPage, ComputeWorkerJobDetailPage } from "../../pages/ComputePage";
import { EvaluationRunDetailPage, EvaluationSuiteDetailPage, EvaluationsPage } from "../../pages/EvaluationsPage";
import { GoalDetailPage, GoalsPage } from "../../pages/GoalsPage";
import { ConnectionDetailPage, ConnectorDefinitionDetailPage, IntegrationsPage } from "../../pages/IntegrationsPage";
import { GovernancePage, ProposalGovernanceDetailPage, SpecificationGovernanceDetailPage } from "../../pages/GovernancePage";
import { LEARNING_REQUIRED_RESOURCES, LearningDetailPage, LearningPage } from "../../pages/LearningPage";
import { MarketplacePage } from "../../pages/MarketplacePage";
import { KnowledgeDetailPage, KnowledgePage, MemoryDetailPage, MemoryPage } from "../../pages/MemoryKnowledgePages";
import { ModelDetailPage, ModelProviderDetailPage } from "../../pages/ModelPages";
import { ModelsPage } from "../../pages/ModelInventoryPage";
import { NotificationsPage } from "../../pages/NotificationsPage";
import { ObservabilityPage } from "../../pages/ObservabilityPage";
import { OnboardingPage } from "../../pages/OnboardingPage";
import { OrganizationsPage } from "../../pages/OrganizationsPage";
import { OverviewPage, UnavailablePage } from "../../pages/Pages";
import { PluginCandidateDetailPage, PluginDetailPage, PluginsPage } from "../../pages/PluginsPage";
import { ProjectDetailPage, WorkspaceDetailPage } from "../../pages/ProjectPages";
import { ProjectsPage } from "../../pages/ProjectListPage";
import { RepositoriesPage, RepositoryDetailPage } from "../../pages/RepositoriesPage";
import { ReferencesPage } from "../../pages/ReferencePages";
import { RunsPage } from "../../pages/RunListPage";
import { SearchPage } from "../../pages/SearchPage";
import { SettingsPage } from "../../pages/SettingsPage";
import { ManagedTasksPage, TaskManagementDetailPage } from "../../pages/TaskManagementPages";
import { TemplateDetailPage, TemplatesPage } from "../../pages/TemplatesPage";
import { TerminalPage } from "../../pages/TerminalPage";
import { UsagePage } from "../../pages/UsagePage";
import { VerificationDetailPage, VerificationPage } from "../../pages/VerificationPage";
import { VerificationBoundReferenceDetailPage, VerificationBoundRunDetailPage, VerificationBoundTaskDetailPage } from "../../pages/VerificationBoundPages";
import { approvalDecisionManifestState } from "../approvalManifest";
import { learningManifestCapabilities } from "../learningManifest";
import { navigation } from "../navigation";
import { matchPath } from "../router";
import type { ShellClients } from "./clients";
import { ManifestResourcePage, ManifestResourcesPage, type ManifestState } from "./manifest";

const EVALUATION_RESOURCES = ["evaluation-suites", "evaluation-runs"] as const;
const COMPUTE_RESOURCES = ["nodes", "workers", "worker-jobs"] as const;
const INTEGRATION_RESOURCES = ["connector-definitions", "connections"] as const;
const KNOWLEDGE_RESOURCES = ["knowledge", "knowledge-results"] as const;

export function renderShellRoute({
  path,
  clients,
  manifest,
  manifestState,
}: {
  path: string;
  clients: ShellClients;
  manifest: APImanifest | null;
  manifestState: ManifestState;
}): ReactNode {
  const {
    client,
    onboardingClient,
    session,
    collections,
    approvalClient,
    repositoryClient,
    conversationClient,
    automationClient,
    goalClient,
    computeClient,
    evaluationClient,
    governanceClient,
    integrationsClient,
    learningClient,
    memoryKnowledgeClient,
    notificationClient,
    organizationClient,
    pluginsClient,
    registryClient,
    templateClient,
    verificationClient,
  } = clients;

  const projectMatch = matchPath("/projects/:projectId", path);
  const workspaceMatch = matchPath("/workspaces/:workspaceId", path);
  const repositoryMatch = matchPath("/repositories/:repositoryId", path);
  const taskManagementMatch = matchPath("/tasks/:taskId/manage", path);
  const taskMatch = matchPath("/tasks/:taskId", path);
  const goalMatch = matchPath("/goals/:goalId", path);
  const governanceProposalMatch = matchPath("/governance/proposals/:proposalId", path);
  const governanceSpecificationMatch = matchPath("/governance/specifications/:specificationId", path);
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
  const learningCandidateMatch = matchPath("/learning/:learningCandidateId", path);
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
  const learningCapabilities = learningManifestCapabilities(manifestState, manifest);

  if (path === "/") return <OverviewPage client={client} />;
  if (path === "/onboarding") return <ManifestResourcePage state={manifestState} manifest={manifest} label="First-run onboarding" resource="onboarding"><OnboardingPage client={client} onboarding={onboardingClient} session={session} manifest={manifest} /></ManifestResourcePage>;
  if (path === "/chat") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Chat" resource="conversations"><ChatPage client={conversationClient} /></ManifestResourcePage>;
  if (path === "/projects") return <ProjectsPage client={client} />;
  if (projectMatch) return <ProjectDetailPage client={client} projectId={projectMatch.projectId} />;
  if (workspaceMatch) return <WorkspaceDetailPage client={client} workspaceId={workspaceMatch.workspaceId} />;
  if (path === "/repositories") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Repositories" resource="repositories"><RepositoriesPage client={repositoryClient} /></ManifestResourcePage>;
  if (repositoryMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Repositories" resource="repositories"><RepositoryDetailPage client={repositoryClient} repositoryId={repositoryMatch.repositoryId} /></ManifestResourcePage>;
  if (path === "/tasks") return <ManagedTasksPage client={client} />;
  if (taskManagementMatch) return <TaskManagementDetailPage client={client} taskId={taskManagementMatch.taskId} />;
  if (taskMatch) return <VerificationBoundTaskDetailPage client={client} verificationClient={verificationClient} taskId={taskMatch.taskId} />;
  if (path === "/goals") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Goals" resource="goals"><GoalsPage client={goalClient} /></ManifestResourcePage>;
  if (goalMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Goals" resource="goals"><GoalDetailPage client={goalClient} goalId={goalMatch.goalId} /></ManifestResourcePage>;
  if (path === "/governance") return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Proposal and Specification governance" resources={["proposals", "specifications"]}><GovernancePage client={governanceClient} /></ManifestResourcesPage>;
  if (governanceProposalMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Proposal governance" resource="proposals"><ProposalGovernanceDetailPage client={governanceClient} proposalId={governanceProposalMatch.proposalId} /></ManifestResourcePage>;
  if (governanceSpecificationMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Specification governance" resource="specifications"><SpecificationGovernanceDetailPage client={governanceClient} specificationId={governanceSpecificationMatch.specificationId} /></ManifestResourcePage>;
  if (path === "/runs") return <RunsPage client={client} />;
  if (runMatch) return <VerificationBoundRunDetailPage client={client} verificationClient={verificationClient} runId={runMatch.runId} />;
  if (path === "/templates") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Templates" resource="templates"><TemplatesPage client={templateClient} /></ManifestResourcePage>;
  if (templateMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Templates" resource="templates"><TemplateDetailPage client={templateClient} templateId={templateMatch.templateId} /></ManifestResourcePage>;
  if (workflowMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Workflow" resource="workflows"><CanonicalConfigurationDetailPage client={collections} collection="workflows" resourceId={workflowMatch.workflowId} /></ManifestResourcePage>;
  if (capabilityAssignmentMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Capability Assignment" resource="capability-assignments"><CanonicalConfigurationDetailPage client={collections} collection="capability-assignments" resourceId={capabilityAssignmentMatch.assignmentId} /></ManifestResourcePage>;
  if (modelRoutingProfileMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Model Routing Profile" resource="model-routing-profiles"><CanonicalConfigurationDetailPage client={collections} collection="model-routing-profiles" resourceId={modelRoutingProfileMatch.profileId} /></ManifestResourcePage>;
  if (path === "/agents") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Agents" resource="agents"><AgentsPage client={client} /></ManifestResourcePage>;
  if (agentMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Agents" resource="agents"><AgentDetailPage client={client} agentId={agentMatch.agentId} /></ManifestResourcePage>;
  if (path === "/agent-teams") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Agent Teams" resource="agent-teams"><AgentTeamsPage client={client} /></ManifestResourcePage>;
  if (agentTeamMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Agent Teams" resource="agent-teams"><AgentTeamDetailPage client={client} teamId={agentTeamMatch.teamId} /></ManifestResourcePage>;
  if (path === "/organizations") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Organizations" resource="organizations"><OrganizationsPage client={organizationClient} /></ManifestResourcePage>;
  if (path === "/files") return <ReferencesPage client={client} />;
  if (referenceMatch) return <VerificationBoundReferenceDetailPage client={client} verificationClient={verificationClient} collection={referenceMatch.collection} resourceId={referenceMatch.resourceId} />;
  if (path === "/memory") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Memory" resource="memory"><MemoryPage client={memoryKnowledgeClient} /></ManifestResourcePage>;
  if (memoryMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Memory" resource="memory"><MemoryDetailPage client={memoryKnowledgeClient} memoryId={memoryMatch.memoryId} /></ManifestResourcePage>;
  if (path === "/knowledge") return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Knowledge" resources={KNOWLEDGE_RESOURCES}><KnowledgePage client={memoryKnowledgeClient} /></ManifestResourcesPage>;
  if (knowledgeMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Knowledge" resources={KNOWLEDGE_RESOURCES}><KnowledgeDetailPage client={memoryKnowledgeClient} sourceId={knowledgeMatch.sourceId} /></ManifestResourcesPage>;
  if (path === "/search") return <SearchPage client={client} />;
  if (path === "/tools") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Tools" resource="capabilities"><CapabilitiesPage client={client} /></ManifestResourcePage>;
  if (capabilityProviderMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Tools" resource="capability-providers"><CapabilityProviderDetailPage client={client} providerId={capabilityProviderMatch.providerId} /></ManifestResourcePage>;
  if (capabilityMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Tools" resource="capabilities"><CapabilityDetailPage client={client} capabilityId={capabilityMatch.capabilityId} /></ManifestResourcePage>;
  if (path === "/integrations") return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Integrations" resources={INTEGRATION_RESOURCES}><IntegrationsPage client={integrationsClient} /></ManifestResourcesPage>;
  if (connectorDefinitionMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Integrations" resources={INTEGRATION_RESOURCES}><ConnectorDefinitionDetailPage client={integrationsClient} definitionId={connectorDefinitionMatch.definitionId} /></ManifestResourcesPage>;
  if (connectionMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Integrations" resources={INTEGRATION_RESOURCES}><ConnectionDetailPage client={integrationsClient} connectionId={connectionMatch.connectionId} /></ManifestResourcesPage>;
  if (path === "/models") return <ModelsPage client={client} />;
  if (providerMatch) return <ModelProviderDetailPage client={client} providerId={providerMatch.providerId} />;
  if (modelMatch) return <ModelDetailPage client={client} modelId={modelMatch.modelId} />;
  if (path === "/evaluations") return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Evaluations" resources={EVALUATION_RESOURCES}><EvaluationsPage client={evaluationClient} /></ManifestResourcesPage>;
  if (evaluationSuiteMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Evaluations" resources={EVALUATION_RESOURCES}><EvaluationSuiteDetailPage client={evaluationClient} suiteRef={evaluationSuiteMatch.suiteRef} /></ManifestResourcesPage>;
  if (evaluationRunMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Evaluations" resources={EVALUATION_RESOURCES}><EvaluationRunDetailPage client={evaluationClient} evaluationRunId={evaluationRunMatch.evaluationRunId} /></ManifestResourcesPage>;
  if (path === "/learning") return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Learning" resources={LEARNING_REQUIRED_RESOURCES}><LearningPage client={learningClient} /></ManifestResourcesPage>;
  if (learningCandidateMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Learning" resources={LEARNING_REQUIRED_RESOURCES}><LearningDetailPage client={learningClient} candidateId={learningCandidateMatch.learningCandidateId} commands={learningCapabilities.commands} postPromotionAvailable={learningCapabilities.postPromotionAvailable} /></ManifestResourcesPage>;
  if (path === "/marketplace") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Marketplace" resource="registry-items"><MarketplacePage client={registryClient} /></ManifestResourcePage>;
  if (path === "/compute") return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}><ComputePage client={computeClient} /></ManifestResourcesPage>;
  if (computeNodeMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}><ComputeNodeDetailPage client={computeClient} nodeId={computeNodeMatch.nodeId} /></ManifestResourcesPage>;
  if (computeWorkerMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}><ComputeWorkerDetailPage client={computeClient} workerId={computeWorkerMatch.workerId} /></ManifestResourcesPage>;
  if (computeWorkerJobMatch) return <ManifestResourcesPage state={manifestState} manifest={manifest} label="Compute" resources={COMPUTE_RESOURCES}><ComputeWorkerJobDetailPage client={computeClient} workerJobId={computeWorkerJobMatch.workerJobId} /></ManifestResourcesPage>;
  if (path === "/plugins") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Plugins" resource="plugins"><PluginsPage client={pluginsClient} candidateAvailable={pluginCandidatesAvailable} /></ManifestResourcePage>;
  if (pluginCandidateMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Plugin discovery" resource="plugin-candidates"><PluginCandidateDetailPage client={pluginsClient} pluginId={pluginCandidateMatch.pluginId} /></ManifestResourcePage>;
  if (pluginMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Plugins" resource="plugins"><PluginDetailPage client={pluginsClient} pluginId={pluginMatch.pluginId} candidateAvailable={pluginCandidatesAvailable} /></ManifestResourcePage>;
  if (path === "/terminal") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Terminal" resource="terminal-sessions"><TerminalPage client={client} /></ManifestResourcePage>;
  if (path === "/automations") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Automations" resource="automations"><AutomationsPage collections={collections} automations={automationClient} /></ManifestResourcePage>;
  if (automationMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Automations" resource="automations"><AutomationDetailPage collections={collections} automations={automationClient} automationId={automationMatch.automationId} /></ManifestResourcePage>;
  if (path === "/verification") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Verification" resource="verifications"><VerificationPage client={verificationClient} /></ManifestResourcePage>;
  if (verificationMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Verification" resource="verifications"><VerificationDetailPage client={verificationClient} verificationId={verificationMatch.verificationId} /></ManifestResourcePage>;
  if (path === "/approvals") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Approvals" resource="approvals"><ApprovalsPage client={approvalClient} decisionState={approvalDecisionState} /></ManifestResourcePage>;
  if (approvalMatch) return <ManifestResourcePage state={manifestState} manifest={manifest} label="Approvals" resource="approvals"><ApprovalDetailPage client={approvalClient} approvalId={approvalMatch.approvalId} decisionState={approvalDecisionState} /></ManifestResourcePage>;
  if (path === "/notifications") return <ManifestResourcePage state={manifestState} manifest={manifest} label="Notifications" resource="notifications"><NotificationsPage client={notificationClient} /></ManifestResourcePage>;
  if (path === "/events") return <ObservabilityPage client={client} view="events" />;
  if (path === "/observability") return <ObservabilityPage client={client} view="observability" />;
  if (path === "/usage") return <UsagePage client={client} manifest={manifest} />;
  if (path === "/settings") return <SettingsPage session={session} />;
  if (navItem) return <UnavailablePage item={navItem} manifest={manifest} />;
  return <UnavailablePage item={{ label: "Unknown route" }} manifest={manifest} />;
}

function referenceRoute(path: string): { collection: ReferenceCollection; resourceId: string } | null {
  for (const collection of ["artifacts", "results", "plans", "steps"] as const) {
    const match = matchPath(`/${collection}/:resourceId`, path);
    if (match) return { collection, resourceId: match.resourceId };
  }
  return null;
}
