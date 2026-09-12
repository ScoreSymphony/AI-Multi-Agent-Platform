import { useMemo } from "react";
import { ApprovalClient } from "../../api/approvals";
import { AutomationClient } from "../../api/automations";
import { BrowserSessionClient } from "../../api/browserSession";
import { ControlPlaneClient } from "../../api/client";
import { ControlPlaneCollectionClient } from "../../api/collections";
import { ConversationClient } from "../../api/conversations";
import { ComputeClient } from "../../api/compute";
import { EvaluationClient } from "../../api/evaluations";
import { GoalClient } from "../../api/goals";
import { GovernanceClient } from "../../api/governance";
import { IntegrationsClient } from "../../api/integrations";
import { LearningClient } from "../../api/learning";
import { MemoryKnowledgeClient } from "../../api/memoryKnowledge";
import { NotificationClient } from "../../api/notifications";
import { OnboardingClient } from "../../api/onboarding";
import { OrganizationClient } from "../../api/organizations";
import { PluginsClient } from "../../api/plugins";
import { RegistryClient } from "../../api/registry";
import { RepositoryCollectionClient } from "../../api/repositories";
import { TemplateClient } from "../../api/templates";
import { VerificationClient } from "../../api/verification";

export function useShellClients(baseUrl: string) {
  const session = useMemo(() => new BrowserSessionClient({ baseUrl }), [baseUrl]);
  const transport = session.transport;
  const fetchImpl = session.fetch;
  const client = useMemo(() => new ControlPlaneClient({ transport }), [transport]);
  const onboardingClient = useMemo(() => new OnboardingClient({ transport }), [transport]);
  const collections = useMemo(() => new ControlPlaneCollectionClient({ transport }), [transport]);
  const approvalClient = useMemo(() => new ApprovalClient({ transport }), [transport]);
  const repositoryClient = useMemo(() => new RepositoryCollectionClient({ transport }), [transport]);
  const conversationClient = useMemo(() => new ConversationClient({ baseUrl, fetchImpl }), [baseUrl, fetchImpl]);
  const automationClient = useMemo(() => new AutomationClient({ transport }), [transport]);
  const goalClient = useMemo(() => new GoalClient({ transport }), [transport]);
  const computeClient = useMemo(() => new ComputeClient({ transport }), [transport]);
  const evaluationClient = useMemo(() => new EvaluationClient({ transport }), [transport]);
  const governanceClient = useMemo(() => new GovernanceClient({ transport }), [transport]);
  const integrationsClient = useMemo(() => new IntegrationsClient({ transport }), [transport]);
  const learningClient = useMemo(() => new LearningClient({ transport }), [transport]);
  const memoryKnowledgeClient = useMemo(() => new MemoryKnowledgeClient({ transport }), [transport]);
  const notificationClient = useMemo(() => new NotificationClient({ transport }), [transport]);
  const organizationClient = useMemo(() => new OrganizationClient({ transport }), [transport]);
  const pluginsClient = useMemo(() => new PluginsClient({ transport }), [transport]);
  const registryClient = useMemo(() => new RegistryClient({ transport }), [transport]);
  const templateClient = useMemo(() => new TemplateClient({ transport }), [transport]);
  const verificationClient = useMemo(() => new VerificationClient({ transport }), [transport]);

  return {
    session,
    client,
    onboardingClient,
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
  };
}

export type ShellClients = ReturnType<typeof useShellClients>;
