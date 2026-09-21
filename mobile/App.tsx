import { useEffect, useState, type ReactNode } from "react";
import { CameraView, useCameraPermissions } from "expo-camera";
import {
  ActivityIndicator,
  Button,
  Linking,
  Platform,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  MOBILE_ANDROID_VERSION_CODE,
  MOBILE_APP_VERSION,
  SUPPORTED_CONTROL_PLANE_API_VERSION,
} from "./src/appIdentity";
import {
  MobileControlPlaneClient,
  MobileControlPlaneError,
  OfflineMutationError,
} from "./src/client";
import {
  classifyConnectionError,
  connectionStateMessage,
  probeControlPlane,
  type ControlPlaneCompatibility,
  type MobileConnectionState,
} from "./src/connection";
import { parseMobileDeepLink, type MobileRouteKind } from "./src/deepLinks";
import { ExpoSecureSecretStorage } from "./src/secureStore";
import {
  MobileSessionStore,
  parseMobilePairingUri,
  type MobilePairingDescriptor,
  type ServerProfile,
} from "./src/session";
import {
  discoverOfficialMobileUpdate,
  requireOfficialUpdateUrl,
  type MobileUpdateInfo,
} from "./src/updates";
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
  const [connectionState, setConnectionState] =
    useState<MobileConnectionState>("never_configured");
  const [compatibility, setCompatibility] =
    useState<ControlPlaneCompatibility | null>(null);
  const [profiles, setProfiles] = useState<ServerProfile[]>([]);
  const [activeProfile, setActiveProfile] = useState<ServerProfile | null>(null);
  const [updateInfo, setUpdateInfo] = useState<MobileUpdateInfo | null>(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  const [profileName, setProfileName] = useState("My platform");
  const [serverUrl, setServerUrl] = useState("https://");
  const [pairingCode, setPairingCode] = useState("");
  const [deviceName, setDeviceName] = useState("My phone");
  const [pairingPreview, setPairingPreview] = useState<MobilePairingDescriptor | null>(null);
  const [scanning, setScanning] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();

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

  function makeClient(profileId: string, baseUrl: string): MobileControlPlaneClient {
    return new MobileControlPlaneClient({
      baseUrl,
      credentialSource: {
        getToken: () => sessionStore.getToken(profileId),
      },
      onUnauthorized: async () => {
        await sessionStore.clear(profileId);
        setActor(null);
        setClient(null);
        setConnectionState("authentication_expired");
        setNotice(connectionStateMessage("authentication_expired"));
      },
    });
  }

  function resetCanonicalProjection(): void {
    setTasks([]);
    setRuns([]);
    setResults([]);
    setArtifacts([]);
    setAgents([]);
    setWorkers([]);
    setApprovals([]);
    setVerifications([]);
    setNotifications([]);
    setSearchResults([]);
    setHealth("unknown");
    setStale(false);
  }

  async function reloadProfiles(): Promise<ServerProfile[]> {
    const saved = await sessionStore.listProfiles();
    setProfiles(saved);
    return saved;
  }

  async function connectProfile(profileId: string, announce = true): Promise<void> {
    setBusy(true);
    setConnectionState("connecting");
    setNotice(null);
    setCompatibility(null);
    resetCanonicalProjection();
    setActor(null);
    setClient(null);
    try {
      const profile = await sessionStore.selectProfile(profileId);
      setActiveProfile(profile);
      setServerUrl(profile.baseUrl);
      setProfileName(profile.displayName);

      const compatibilityResult = await probeControlPlane(
        profile.baseUrl,
        SUPPORTED_CONTROL_PLANE_API_VERSION,
      );
      setCompatibility(compatibilityResult);

      const next = makeClient(profile.id, profile.baseUrl);
      const currentActor = await next.me();
      const updated = await sessionStore.recordSuccessfulConnection(
        profile.id,
        compatibilityResult,
      );
      setActiveProfile(updated);
      setActor(currentActor);
      setClient(next);
      setConnectionState("connected");
      await reloadProfiles();
      if (announce) setNotice(`Connected to ${updated.displayName}.`);
    } catch (error) {
      const state = classifyConnectionError(error);
      setConnectionState(state);
      setNotice(connectionStateMessage(state));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const saved = await sessionStore.listProfiles();
        if (cancelled) return;
        setProfiles(saved);

        const selected = await sessionStore.selectedProfile();
        if (cancelled) return;
        if (selected) {
          setActiveProfile(selected);
          setServerUrl(selected.baseUrl);
          setProfileName(selected.displayName);
        }

        const session = await sessionStore.current();
        if (cancelled) return;
        if (!session) {
          setConnectionState(selected ? "authentication_expired" : "never_configured");
          return;
        }
        await connectProfile(session.profileId, false);
      } catch (error) {
        if (!cancelled) {
          const state = classifyConnectionError(error);
          setConnectionState(state);
          setNotice(messageFor(error));
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
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

  async function finishPairing(
    descriptor: MobilePairingDescriptor,
  ): Promise<void> {
    setBusy(true);
    setConnectionState("connecting");
    setNotice(null);
    try {
      const compatibilityResult = await probeControlPlane(
        descriptor.baseUrl,
        SUPPORTED_CONTROL_PLANE_API_VERSION,
      );
      const currentActor = await sessionStore.pair(
        descriptor,
        deviceName,
        Platform.OS,
        globalThis.fetch.bind(globalThis),
        profileName,
      );
      const session = await sessionStore.current();
      if (!session) throw new Error("Secure pairing did not persist");
      const updated = await sessionStore.recordSuccessfulConnection(
        session.profileId,
        compatibilityResult,
      );
      setPairingCode("");
      setPairingPreview(null);
      setScanning(false);
      resetCanonicalProjection();
      setCompatibility(compatibilityResult);
      setActiveProfile(updated);
      setActor(currentActor);
      setClient(makeClient(session.profileId, session.baseUrl));
      setServerUrl(session.baseUrl);
      setProfileName(updated.displayName);
      setConnectionState("connected");
      await reloadProfiles();
      setNotice("Mobile device paired successfully.");
    } catch (error) {
      const state = classifyConnectionError(error);
      setConnectionState(state);
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
      setReady(true);
    }
  }

  async function pairFallbackCode(): Promise<void> {
    setBusy(true);
    setConnectionState("connecting");
    setNotice(null);
    try {
      const compatibilityResult = await probeControlPlane(
        serverUrl,
        SUPPORTED_CONTROL_PLANE_API_VERSION,
      );
      const currentActor = await sessionStore.pairWithCode(
        serverUrl,
        pairingCode,
        deviceName,
        Platform.OS,
        globalThis.fetch.bind(globalThis),
        profileName,
      );
      const session = await sessionStore.current();
      if (!session) throw new Error("Secure pairing did not persist");
      const updated = await sessionStore.recordSuccessfulConnection(
        session.profileId,
        compatibilityResult,
      );
      setPairingCode("");
      resetCanonicalProjection();
      setCompatibility(compatibilityResult);
      setActiveProfile(updated);
      setActor(currentActor);
      setClient(makeClient(session.profileId, session.baseUrl));
      setServerUrl(session.baseUrl);
      setProfileName(updated.displayName);
      setConnectionState("connected");
      await reloadProfiles();
      setNotice("Mobile device paired successfully.");
    } catch (error) {
      const state = classifyConnectionError(error);
      setConnectionState(state);
      setNotice(messageFor(error));
    } finally {
      setBusy(false);
      setReady(true);
    }
  }

  async function startScanner(): Promise<void> {
    const permission = cameraPermission?.granted
      ? cameraPermission
      : await requestCameraPermission();
    if (!permission.granted) {
      setNotice("Camera permission is required to scan a pairing QR code.");
      return;
    }
    setScanning(true);
    setNotice(null);
  }

  function previewScannedPairing(value: string): void {
    try {
      const descriptor = parseMobilePairingUri(value);
      setPairingPreview(descriptor);
      setServerUrl(descriptor.baseUrl);
      setScanning(false);
      setNotice(null);
    } catch (error) {
      setScanning(false);
      setNotice(messageFor(error));
    }
  }

  async function signOut(): Promise<void> {
    if (activeProfile) await sessionStore.clear(activeProfile.id);
    setActor(null);
    setClient(null);
    resetCanonicalProjection();
    setConnectionState(activeProfile ? "authentication_expired" : "never_configured");
    setNotice("Credential removed from this device. The non-secret server profile was retained.");
  }

  async function removeProfile(profileId: string): Promise<void> {
    const removingActive = activeProfile?.id === profileId;
    await sessionStore.removeProfile(profileId);
    const saved = await reloadProfiles();
    if (removingActive) {
      setActor(null);
      setClient(null);
      setCompatibility(null);
      setActiveProfile(null);
      resetCanonicalProjection();
      setConnectionState(saved.length ? "authentication_expired" : "never_configured");
    }
    setNotice("Server profile and its device credential were removed.");
  }

  async function checkForUpdates(): Promise<void> {
    setCheckingUpdate(true);
    const result = await discoverOfficialMobileUpdate(MOBILE_APP_VERSION);
    setUpdateInfo(result);
    setCheckingUpdate(false);
  }

  async function openOfficialUpdate(url: string): Promise<void> {
    try {
      await Linking.openURL(requireOfficialUpdateUrl(url));
    } catch (error) {
      setNotice(messageFor(error));
    }
  }

  function serverSupports(resource: string): boolean {
    if (!compatibility || compatibility.resources.length === 0) return true;
    return compatibility.resources.includes(resource);
  }

  async function refreshCurrentTab(current: Tab): Promise<void> {
    if (!client) return;
    setBusy(true);
    setNotice(null);
    try {
      if (current === "dashboard") {
        const [healthResult, taskResult, runResult] = await Promise.all([
          client.health(),
          client.listTasks(),
          client.listRuns(),
        ]);
        const agentResult = serverSupports("agents") ? await client.listAgents() : null;
        const workerResult = serverSupports("workers") ? await client.listWorkers() : null;
        setHealth(healthResult.data.status);
        setTasks(taskResult.data.items);
        setRuns(runResult.data.items);
        setAgents(agentResult?.data.items ?? []);
        setWorkers(workerResult?.data.items ?? []);
        const staleNow =
          healthResult.stale ||
          taskResult.stale ||
          runResult.stale ||
          Boolean(agentResult?.stale) ||
          Boolean(workerResult?.stale);
        setStale(staleNow);
        setConnectionState(staleNow ? "offline" : "connected");
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
        const staleNow =
          taskResult.stale ||
          runResult.stale ||
          resultResult.stale ||
          artifactResult.stale;
        setStale(staleNow);
        setConnectionState(staleNow ? "offline" : "connected");
      } else if (current === "decisions") {
        const approvalResult = serverSupports("approvals")
          ? await client.listApprovals()
          : null;
        const verificationResult = serverSupports("verification-reviews")
          ? await client.listPendingVerification()
          : null;
        setApprovals(approvalResult?.data.items ?? []);
        setVerifications(verificationResult?.data.items ?? []);
        const staleNow = Boolean(approvalResult?.stale || verificationResult?.stale);
        setStale(staleNow);
        setConnectionState(staleNow ? "offline" : "connected");
      } else if (current === "notifications") {
        if (!serverSupports("notifications")) {
          setNotifications([]);
          setStale(false);
          setNotice("This server does not advertise the optional Notifications surface.");
          return;
        }
        const notificationResult = await client.listNotifications();
        setNotifications(notificationResult.data.items);
        setStale(notificationResult.stale);
        setConnectionState(notificationResult.stale ? "offline" : "connected");
      }
    } catch (error) {
      const state = classifyConnectionError(error);
      setConnectionState(state);
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
    if (!serverSupports("search")) {
      setSearchResults([]);
      setNotice("This server does not advertise the optional Search surface.");
      return;
    }
    setBusy(true);
    try {
      const response = await client.search(searchQuery);
      setSearchResults(response.data.items);
      setStale(response.stale);
      setConnectionState(response.stale ? "offline" : "connected");
    } catch (error) {
      const state = classifyConnectionError(error);
      setConnectionState(state);
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
            Pair this phone from an already authenticated Web or CLI session. The QR/code is
            short-lived and single-use; the resulting device credential is kept in OS secure storage.
          </Text>

          <Text style={styles.label}>Device name</Text>
          <TextInput
            value={deviceName}
            onChangeText={setDeviceName}
            style={styles.input}
          />

          {pairingPreview ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Confirm server identity</Text>
              <Text selectable>{pairingPreview.baseUrl}</Text>
              <Text style={styles.muted}>
                Only continue if this is the Control Plane you intended to pair with.
              </Text>
              <View style={styles.actions}>
                <Button
                  title={busy ? "Pairing…" : "Trust and pair"}
                  onPress={() => void finishPairing(pairingPreview)}
                  disabled={busy}
                />
                <Button
                  title="Cancel"
                  onPress={() => setPairingPreview(null)}
                  disabled={busy}
                />
              </View>
            </View>
          ) : null}

          {scanning ? (
            <View style={styles.scanner}>
              <CameraView
                style={styles.camera}
                facing="back"
                barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
                onBarcodeScanned={({ data }) => previewScannedPairing(data)}
              />
              <Button title="Cancel scanner" onPress={() => setScanning(false)} />
            </View>
          ) : (
            <Button
              title="Scan pairing QR"
              onPress={() => void startScanner()}
              disabled={busy}
            />
          )}

          <Text style={styles.label}>Fallback code</Text>
          <Text style={styles.muted}>
            If scanning is unavailable, enter the HTTPS Control Plane origin and the code shown by the trusted session.
          </Text>
          <TextInput
            autoCapitalize="none"
            autoCorrect={false}
            value={serverUrl}
            onChangeText={setServerUrl}
            style={styles.input}
            placeholder="https://platform.example"
          />
          <TextInput
            autoCapitalize="characters"
            autoCorrect={false}
            value={pairingCode}
            onChangeText={setPairingCode}
            style={styles.input}
            placeholder="XXXX-XXXX-XXXX"
          />
          <Button
            title={busy ? "Pairing…" : "Pair with code"}
            onPress={() => void pairFallbackCode()}
            disabled={busy}
          />
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
  scanner: { minHeight: 320, gap: 8 },
  camera: { minHeight: 280, borderRadius: 10, overflow: "hidden" },
});
