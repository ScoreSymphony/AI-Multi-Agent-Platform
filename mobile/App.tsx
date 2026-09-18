import { useEffect, useState, type ReactNode } from "react";
import {
  ActivityIndicator,
  Button,
  Linking,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  MobileControlPlaneClient,
  MobileControlPlaneError,
  OfflineMutationError,
} from "./src/client";
import { parseMobileDeepLink, type MobileRouteKind } from "./src/deepLinks";
import { ExpoSecureSecretStorage } from "./src/secureStore";
import { MobileSessionStore } from "./src/session";
import type {
  AuthenticatedActor,
  CanonicalAgent,
  CanonicalApproval,
  CanonicalNotification,
  CanonicalReference,
  CanonicalRun,
  CanonicalTask,
  CanonicalVerification,
  CanonicalWorker,
  SearchResult,
} from "./src/types";

type Tab = "dashboard" | "work" | "decisions" | "notifications" | "search";

const sessionStore = new MobileSessionStore(new ExpoSecureSecretStorage());

export default function App() {
  const [ready, setReady] = useState(false);
  const [client, setClient] = useState<MobileControlPlaneClient | null>(null);
  const [actor, setActor] = useState<AuthenticatedActor | null>(null);
  const [tab, setTab] = useState<Tab>("dashboard");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);

  const [serverUrl, setServerUrl] = useState("https://");
  const [token, setToken] = useState("");

  const [tasks, setTasks] = useState<CanonicalTask[]>([]);
  const [runs, setRuns] = useState<CanonicalRun[]>([]);
  const [results, setResults] = useState<CanonicalReference[]>([]);
  const [artifacts, setArtifacts] = useState<CanonicalReference[]>([]);
  const [agents, setAgents] = useState<CanonicalAgent[]>([]);
  const [workers, setWorkers] = useState<CanonicalWorker[]>([]);
  const [approvals, setApprovals] = useState<CanonicalApproval[]>([]);
  const [verifications, setVerifications] = useState<CanonicalVerification[]>([]);
  const [notifications, setNotifications] = useState<CanonicalNotification[]>([]);
  const [health, setHealth] = useState<string>("unknown");

  const [taskTitle, setTaskTitle] = useState("");
  const [taskObjective, setTaskObjective] = useState("");
  const [takeoverMessageId, setTakeoverMessageId] = useState("");
  const [takeoverTaskId, setTakeoverTaskId] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);

  function makeClient(baseUrl: string): MobileControlPlaneClient {
    return new MobileControlPlaneClient({
      baseUrl,
      credentialSource: sessionStore,
      onUnauthorized: async () => {
        await sessionStore.clear();
        setActor(null);
        setClient(null);
        setNotice("The credential was revoked or expired. Sign in again.");
      },
    });
  }

  useEffect(() => {
    let cancelled = false;
    void sessionStore.current().then(async (session) => {
      if (!session || cancelled) {
        setReady(true);
        return;
      }
      const next = makeClient(session.baseUrl);
      try {
        const currentActor = await next.me();
        if (!cancelled) {
          setServerUrl(session.baseUrl);
          setActor(currentActor);
          setClient(next);
        }
      } catch (error) {
        if (error instanceof MobileControlPlaneError && error.status === 401) {
          await sessionStore.clear();
        } else if (!cancelled) {
          setNotice("Stored session could not be verified. Check network connectivity.");
          setServerUrl(session.baseUrl);
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handle = (url: string | null) => {
      if (!url) return;
      const route = parseMobileDeepLink(url);
      if (!route) {
        setNotice("Rejected an invalid or unsupported deep link.");
        return;
      }
      setTab(tabForRoute(route.kind));
      setNotice(`Opened canonical ${route.kind} ${route.id}. Refresh to read server state.`);
    };

    void Linking.getInitialURL().then(handle);
    const subscription = Linking.addEventListener("url", ({ url }) => handle(url));
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (!client || !actor) return;
    void refreshCurrentTab(tab);
  }, [client, actor, tab]);

  async function activate(): Promise<void> {
    setBusy(true);
    setNotice(null);
    try {
      const currentActor = await sessionStore.activate(serverUrl, token);
      const session = await sessionStore.current();
      if (!session) throw new Error("Secure session activation did not persist");
      setToken("");
      setActor(currentActor);
      setClient(makeClient(session.baseUrl));
      setServerUrl(session.baseUrl);
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
      setReady(true);
    }
  }

  async function signOut(): Promise<void> {
    await sessionStore.clear();
    setActor(null);
    setClient(null);
    setTasks([]);
    setRuns([]);
    setNotice("Credential removed from this device.");
  }

  async function refreshCurrentTab(current: Tab): Promise<void> {
    if (!client) return;
    setBusy(true);
    setNotice(null);
    try {
      if (current === "dashboard") {
        const [healthResult, taskResult, runResult, agentResult, workerResult] =
          await Promise.all([
            client.health(),
            client.listTasks(),
            client.listRuns(),
            client.listAgents(),
            client.listWorkers(),
          ]);
        setHealth(healthResult.data.status);
        setTasks(taskResult.data.items);
        setRuns(runResult.data.items);
        setAgents(agentResult.data.items);
        setWorkers(workerResult.data.items);
        setStale(
          healthResult.stale ||
            taskResult.stale ||
            runResult.stale ||
            agentResult.stale ||
            workerResult.stale,
        );
      } else if (current === "work") {
        const [taskResult, runResult, resultResult, artifactResult] = await Promise.all([
          client.listTasks(),
          client.listRuns(),
          client.listResults(),
          client.listArtifacts(),
        ]);
        setTasks(taskResult.data.items);
        setRuns(runResult.data.items);
        setResults(resultResult.data.items);
        setArtifacts(artifactResult.data.items);
        setStale(
          taskResult.stale ||
            runResult.stale ||
            resultResult.stale ||
            artifactResult.stale,
        );
      } else if (current === "decisions") {
        const [approvalResult, verificationResult] = await Promise.all([
          client.listApprovals(),
          client.listPendingVerification(),
        ]);
        setApprovals(approvalResult.data.items);
        setVerifications(verificationResult.data.items);
        setStale(approvalResult.stale || verificationResult.stale);
      } else if (current === "notifications") {
        const notificationResult = await client.listNotifications();
        setNotifications(notificationResult.data.items);
        setStale(notificationResult.stale);
      }
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function submitTask(): Promise<void> {
    if (!client || !actor) return;
    setBusy(true);
    try {
      await client.createTask(actor, taskTitle, taskObjective);
      setTaskTitle("");
      setTaskObjective("");
      await refreshCurrentTab("work");
      setNotice("Task submitted to the canonical Control Plane.");
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function decideApproval(
    approval: CanonicalApproval,
    decision: "approve" | "deny",
  ): Promise<void> {
    if (!client) return;
    setBusy(true);
    try {
      if (decision === "approve") await client.approve(approval);
      else await client.deny(approval);
      await refreshCurrentTab("decisions");
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function resumeWaitingTask(): Promise<void> {
    if (!client) return;
    setBusy(true);
    try {
      const task = await client.resumeWaitingTask(takeoverMessageId, takeoverTaskId);
      setTakeoverMessageId("");
      setTakeoverTaskId("");
      setNotice(`Resumed canonical waiting Task ${task.id} from Conversation input.`);
      await refreshCurrentTab("decisions");
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function reviewVerification(
    verification: CanonicalVerification,
    action: "verification.accept" | "verification.reject" | "verification.request-changes",
  ): Promise<void> {
    if (!client) return;
    setBusy(true);
    try {
      await client.verificationReview(verification.id, action);
      await refreshCurrentTab("decisions");
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function markRead(notification: CanonicalNotification): Promise<void> {
    if (!client) return;
    try {
      await client.markNotificationRead(notification.id);
      await refreshCurrentTab("notifications");
    } catch (error) {
      setNotice(messageFor(error));
    }
  }

  async function runSearch(): Promise<void> {
    if (!client) return;
    setBusy(true);
    try {
      const response = await client.search(searchQuery);
      setSearchResults(response.data.items);
      setStale(response.stale);
    } catch (error) {
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator />
        <Text>Checking secure mobile session…</Text>
      </SafeAreaView>
    );
  }

  if (!client || !actor) {
    return (
      <SafeAreaView style={styles.screen}>
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={styles.title}>AI Multi-Agent Platform</Text>
          <Text style={styles.muted}>
            Mobile uses an already-issued bearer credential and validates it against
            /api/v1/auth/me. Remote servers require TLS.
          </Text>
          <Text style={styles.label}>Control Plane URL</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            value={serverUrl}
            onChangeText={setServerUrl}
            style={styles.input}
          />
          <Text style={styles.label}>Bearer credential</Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            value={token}
            onChangeText={setToken}
            style={styles.input}
          />
          <Button title={busy ? "Validating…" : "Activate secure session"} onPress={() => void activate()} disabled={busy} />
          {notice ? <Text style={styles.notice}>{notice}</Text> : null}
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>AI Multi-Agent Platform</Text>
          <Text style={styles.muted}>{actor.actor_type}:{actor.actor_id}</Text>
        </View>
        <Button title="Sign out" onPress={() => void signOut()} />
      </View>

      <View style={styles.tabs}>
        {(["dashboard", "work", "decisions", "notifications", "search"] as Tab[]).map(
          (item) => (
            <Button
              key={item}
              title={item}
              disabled={tab === item}
              onPress={() => setTab(item)}
            />
          ),
        )}
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {stale ? (
          <Text style={styles.stale}>
            Showing cached read-only data. Mutations are disabled until a fresh Control Plane read succeeds.
          </Text>
        ) : null}
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}
        {busy ? <ActivityIndicator /> : null}

        {tab === "dashboard" ? (
          <>
            <Section title="Status">
              <Text>Control Plane: {health}</Text>
              <Text>Tasks: {tasks.length}</Text>
              <Text>Runs: {runs.length}</Text>
            </Section>
            <Section title="Agents">
              {agents.map((agent) => (
                <Row key={agent.id} primary={agent.id} secondary={String(agent.type)} />
              ))}
            </Section>
            <Section title="Workers">
              {workers.map((worker) => (
                <Row key={worker.id} primary={worker.id} secondary={worker.status ?? String(worker.type)} />
              ))}
            </Section>
          </>
        ) : null}

        {tab === "work" ? (
          <>
            <Section title="Submit lightweight Task">
              <TextInput placeholder="Title" value={taskTitle} onChangeText={setTaskTitle} style={styles.input} />
              <TextInput
                placeholder="Objective"
                value={taskObjective}
                onChangeText={setTaskObjective}
                style={[styles.input, styles.multiline]}
                multiline
              />
              <Button title="Submit Task" onPress={() => void submitTask()} disabled={busy || stale} />
            </Section>
            <Section title="Tasks">
              {tasks.map((task) => (
                <Row key={task.id} primary={task.title} secondary={`${task.status} · ${task.id}`} />
              ))}
            </Section>
            <Section title="Runs">
              {runs.map((run) => (
                <Row key={run.id} primary={run.id} secondary={`${run.status} · Task ${run.task_id}`} />
              ))}
            </Section>
            <Section title="Results">
              {results.map((result) => (
                <Row key={result.id} primary={result.id} secondary={`Task ${result.task_id}`} />
              ))}
            </Section>
            <Section title="Artifacts">
              {artifacts.map((artifact) => (
                <Row key={artifact.id} primary={artifact.id} secondary={`Task ${artifact.task_id}`} />
              ))}
            </Section>
          </>
        ) : null}

        {tab === "decisions" ? (
          <>
            <Section title="Approvals">
              {approvals.map((approval) => (
                <View key={approval.id} style={styles.card}>
                  <Text style={styles.cardTitle}>{approval.action}</Text>
                  <Text>{approval.resource_type}:{approval.resource_id}</Text>
                  <Text>Risk: {approval.risk}</Text>
                  <Text>Reason: {approval.reason}</Text>
                  <Text selectable>Digest: {approval.requested_action_digest}</Text>
                  {approval.status === "pending" ? (
                    <View style={styles.actions}>
                      <Button title="Approve" disabled={stale || busy} onPress={() => void decideApproval(approval, "approve")} />
                      <Button title="Deny" disabled={stale || busy} onPress={() => void decideApproval(approval, "deny")} />
                    </View>
                  ) : null}
                </View>
              ))}
            </Section>
            <Section title="Human takeover / waiting Task">
              <Text style={styles.muted}>
                Resume only from an already persisted authenticated user Conversation message.
                The canonical Task remains server-owned.
              </Text>
              <TextInput
                placeholder="Conversation message ID"
                value={takeoverMessageId}
                onChangeText={setTakeoverMessageId}
                style={styles.input}
              />
              <TextInput
                placeholder="Waiting Task ID"
                value={takeoverTaskId}
                onChangeText={setTakeoverTaskId}
                style={styles.input}
              />
              <Button
                title="Resume waiting Task"
                disabled={stale || busy}
                onPress={() => void resumeWaitingTask()}
              />
            </Section>
            <Section title="Verification review">
              {verifications.map((verification) => (
                <View key={verification.id} style={styles.card}>
                  <Text style={styles.cardTitle}>{verification.id}</Text>
                  <Text>Task: {verification.task_id}</Text>
                  <Text>Status: {verification.status}</Text>
                  <View style={styles.actions}>
                    <Button title="Accept" disabled={stale || busy} onPress={() => void reviewVerification(verification, "verification.accept")} />
                    <Button title="Reject" disabled={stale || busy} onPress={() => void reviewVerification(verification, "verification.reject")} />
                    <Button title="Changes" disabled={stale || busy} onPress={() => void reviewVerification(verification, "verification.request-changes")} />
                  </View>
                </View>
              ))}
            </Section>
          </>
        ) : null}

        {tab === "notifications" ? (
          <Section title="Notifications">
            {notifications.map((notification) => (
              <View key={notification.id} style={styles.card}>
                <Text style={styles.cardTitle}>{notification.title}</Text>
                <Text>{notification.severity} · {notification.category} · {notification.state}</Text>
                {notification.state === "unread" ? (
                  <Button title="Mark read" disabled={stale || busy} onPress={() => void markRead(notification)} />
                ) : null}
              </View>
            ))}
          </Section>
        ) : null}

        {tab === "search" ? (
          <Section title="Search canonical history">
            <TextInput
              placeholder="Search Tasks, Runs, Agents…"
              value={searchQuery}
              onChangeText={setSearchQuery}
              style={styles.input}
            />
            <Button title="Search" onPress={() => void runSearch()} disabled={busy} />
            {searchResults.map((result) => (
              <Row key={`${result.type}:${result.id}`} primary={result.title ?? result.id} secondary={`${result.type} · ${result.status ?? ""}`} />
            ))}
          </Section>
        ) : null}

        <Button title="Refresh" onPress={() => void refreshCurrentTab(tab)} disabled={busy} />
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Row({ primary, secondary }: { primary: string; secondary: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.cardTitle}>{primary}</Text>
      <Text style={styles.muted}>{secondary}</Text>
    </View>
  );
}

function tabForRoute(kind: MobileRouteKind): Tab {
  if (kind === "approval" || kind === "verification") return "decisions";
  if (kind === "notification") return "notifications";
  if (kind === "task" || kind === "run" || kind === "artifact" || kind === "result") return "work";
  return "dashboard";
}

function messageFor(error: unknown): string {
  if (error instanceof OfflineMutationError) return error.message;
  if (error instanceof MobileControlPlaneError) {
    return `${error.code}: ${error.message}`;
  }
  return error instanceof Error ? error.message : "Unexpected mobile client error";
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#fff" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12 },
  header: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  headerTitle: { fontSize: 18, fontWeight: "700" },
  tabs: {
    paddingHorizontal: 8,
    paddingVertical: 8,
    gap: 4,
    flexDirection: "row",
    flexWrap: "wrap",
  },
  content: { padding: 16, gap: 16, paddingBottom: 40 },
  title: { fontSize: 28, fontWeight: "700", marginBottom: 8 },
  label: { fontWeight: "600", marginTop: 12 },
  input: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    padding: 12,
    marginVertical: 6,
  },
  multiline: { minHeight: 96, textAlignVertical: "top" },
  section: { gap: 8 },
  sectionTitle: { fontSize: 20, fontWeight: "700" },
  row: { paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  card: {
    padding: 12,
    gap: 6,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    marginBottom: 8,
  },
  cardTitle: { fontWeight: "700" },
  muted: { opacity: 0.65 },
  notice: { padding: 10, borderWidth: StyleSheet.hairlineWidth, borderRadius: 8 },
  stale: { padding: 10, borderWidth: 1, borderRadius: 8, fontWeight: "600" },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6 },
});
