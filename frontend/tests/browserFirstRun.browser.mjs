import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const host = "127.0.0.1";
const backendPort = 8000;
const modelPort = 8001;
const frontendPort = 4174;
const frontendUrl = `http://${host}:${frontendPort}`;
const backendUrl = `http://${host}:${backendPort}`;
const username = "browser-admin";
const password = "correct horse battery staple browser first run";
const dataDir = await mkdtemp(join(tmpdir(), "ai-map-browser-first-run-"));
const artifactDir = process.env.BROWSER_E2E_ARTIFACT_DIR
  ?? join(process.cwd(), ".browser-e2e-artifacts");

let backend;
let vite;
let browser;
let page;
let backendLog = "";
let viteLog = "";

let chatCompletionCount = 0;
const modelServer = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/v1/models") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ data: [{ id: "browser-model-native" }] }));
    return;
  }
  if (request.method === "POST" && request.url === "/v1/chat/completions") {
    request.resume();
    chatCompletionCount += 1;
    const delayMs = chatCompletionCount === 1 ? 250 : 20;
    setTimeout(() => {
      if (response.destroyed) return;
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({
        model: "browser-model-native",
        choices: [{
          message: {
            role: "assistant",
            content: JSON.stringify({ outcome: "pass", findings: [] }),
          },
          finish_reason: "stop",
        }],
        usage: {
          prompt_tokens: 20,
          completion_tokens: 8,
          total_tokens: 28,
        },
      }));
    }, delayMs);
    return;
  }
  response.writeHead(404, { "content-type": "application/json" });
  response.end(JSON.stringify({ error: "not found" }));
});

function listen(server, port) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      server.off("error", reject);
      resolve();
    });
  });
}

function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

function stopProcess(child, label) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();

  return new Promise((resolve, reject) => {
    let forceKillTimer;
    const gracefulTimer = setTimeout(() => {
      child.kill("SIGKILL");
      forceKillTimer = setTimeout(() => {
        cleanup();
        reject(new Error(`${label} did not exit after SIGTERM/SIGKILL`));
      }, 2_000);
    }, 5_000);

    const cleanup = () => {
      clearTimeout(gracefulTimer);
      if (forceKillTimer) clearTimeout(forceKillTimer);
      child.off("close", onClose);
      child.off("error", onError);
    };
    const onClose = () => {
      cleanup();
      resolve();
    };
    const onError = (error) => {
      cleanup();
      reject(error);
    };

    child.once("close", onClose);
    child.once("error", onError);
    child.kill("SIGTERM");
  });
}

async function waitForUrl(url, label, logs, predicate = (response) => response.ok) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(url);
      if (await predicate(response)) return;
    } catch {
      // Process is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`${label} did not start. ${logs()}`);
}

async function waitForButton(page, name) {
  const button = page.getByRole("button", { name, exact: true });
  await button.waitFor();
  return button;
}

async function logoutThroughBrowserClient(page) {
  await page.evaluate(async () => {
    const { BrowserSessionClient } = await import("/src/api/browserSession.ts");
    await new BrowserSessionClient().logout();
  });
}

async function signIn(page) {
  await page.getByRole("heading", { name: "Sign in", exact: true }).waitFor();
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await (await waitForButton(page, "Sign in")).click();
}

async function onboardingStatusSnapshot(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/v1/onboarding/first-run", { credentials: "include" });
    return { statusCode: response.status, body: await response.json() };
  });
}

async function taskInventory(page) {
  return page.evaluate(async () => {
    const [{ BrowserSessionClient }, { ControlPlaneClient }] = await Promise.all([
      import("/src/api/browserSession.ts"),
      import("/src/api/client.ts"),
    ]);
    const session = new BrowserSessionClient();
    const client = new ControlPlaneClient({ transport: session.transport });
    const tasks = await client.listTasks({ limit: 100 });
    return tasks.items
      .map((task) => ({ id: task.id, status: task.status }))
      .sort((left, right) => left.id.localeCompare(right.id));
  });
}

async function standardAgentTeamInventory(page) {
  return page.evaluate(async () => {
    const [{ BrowserSessionClient }, { ControlPlaneClient }] = await Promise.all([
      import("/src/api/browserSession.ts"),
      import("/src/api/client.ts"),
    ]);
    const session = new BrowserSessionClient();
    const client = new ControlPlaneClient({ transport: session.transport });
    const teams = await client.listAgentTeams({ limit: 100 });
    return teams.items.map((team) => ({
      id: team.id,
      starterKey: team.revision.profile.metadata.starter_key ?? null,
      starterKind: team.revision.profile.metadata.starter_kind ?? null,
    }));
  });
}

async function readPublicApiResource(page, path) {
  return page.evaluate(async (resourcePath) => {
    const { BrowserSessionClient } = await import("/src/api/browserSession.ts");
    const session = new BrowserSessionClient();
    return session.transport.request(resourcePath, { retry: "never" });
  }, path);
}

async function publicApiCommand(page, path, body) {
  return page.evaluate(async ({ commandPath, commandBody }) => {
    const { BrowserSessionClient } = await import("/src/api/browserSession.ts");
    const session = new BrowserSessionClient();
    return session.transport.request(commandPath, {
      method: "POST",
      body: commandBody,
      idempotencyKey: crypto.randomUUID(),
      retry: "never",
    });
  }, { commandPath: path, commandBody: body });
}

function requireCanonicalIdentity(resource, expectedId, label) {
  if (typeof expectedId !== "string" || expectedId.length === 0 || !resource || resource.id !== expectedId) {
    throw new Error(
      `${label} did not resolve the expected canonical ID ${expectedId}: ${JSON.stringify(resource)}`,
    );
  }
}

async function refreshProviderHealthThroughBrowserClient(page, providerId) {
  return page.evaluate(async (id) => {
    const [{ BrowserSessionClient }, { ControlPlaneClient }] = await Promise.all([
      import("/src/api/browserSession.ts"),
      import("/src/api/client.ts"),
    ]);
    const session = new BrowserSessionClient();
    const client = new ControlPlaneClient({ transport: session.transport });
    return client.refreshModelProviderHealth(id);
  }, providerId);
}

function requireText(haystack, needle, label) {
  if (!haystack.includes(needle)) {
    throw new Error(`${label} missing ${JSON.stringify(needle)} from ${JSON.stringify(haystack)}`);
  }
}

async function assertModelSetupContract(page) {
  const snapshot = await page.evaluate(async () => {
    const [statusResponse, manifestResponse] = await Promise.all([
      fetch("/api/v1/onboarding/first-run", { credentials: "include" }),
      fetch("/api/v1/", { credentials: "include" }),
    ]);
    return {
      statusCode: statusResponse.status,
      status: await statusResponse.json(),
      manifestCode: manifestResponse.status,
      manifest: await manifestResponse.json(),
    };
  });

  if (snapshot.statusCode !== 200) {
    throw new Error(`Authenticated onboarding status returned ${snapshot.statusCode}: ${JSON.stringify(snapshot.status)}`);
  }
  if (snapshot.manifestCode !== 200) {
    throw new Error(`Authenticated API manifest returned ${snapshot.manifestCode}: ${JSON.stringify(snapshot.manifest)}`);
  }
  if (!snapshot.status.installed_model_adapter_ids?.includes("openai-compatible")) {
    throw new Error(`Default single-node onboarding adapter missing from status: ${JSON.stringify(snapshot.status)}`);
  }
  if (Array.isArray(snapshot.manifest.commands) && !snapshot.manifest.commands.includes("onboarding.configure-model")) {
    throw new Error(`onboarding.configure-model missing from authenticated manifest: ${JSON.stringify(snapshot.manifest)}`);
  }
  const adapterField = snapshot.status.model_setup?.fields?.find((field) => field.path === "adapter_id");
  if (!adapterField || adapterField.label !== "Installed adapter" || !adapterField.options?.includes("openai-compatible")) {
    throw new Error(`Backend-owned model setup contract is incomplete: ${JSON.stringify(snapshot.status.model_setup)}`);
  }
}

try {
  await listen(modelServer, modelPort);

  backend = spawn(
    process.env.PYTHON ?? "python",
    ["-m", "ai_multi_agent_platform.adapters.single_node_app", "serve"],
    {
      cwd: "..",
      env: {
        ...process.env,
        AI_MAP_DATA_DIR: dataDir,
        AI_MAP_HOST: host,
        AI_MAP_PORT: String(backendPort),
        AI_MAP_SECURE_COOKIE: "false",
        AI_MAP_LOG_LEVEL: "warning",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  backend.stdout.on("data", (chunk) => {
    backendLog += chunk.toString();
  });
  backend.stderr.on("data", (chunk) => {
    backendLog += chunk.toString();
  });
  await waitForUrl(
    `${backendUrl}/api/v1/auth/bootstrap-status`,
    "Single-node backend",
    () => backendLog,
    async (response) => response.ok && (await response.json()).state === "uninitialized",
  );

  vite = spawn(
    process.execPath,
    ["node_modules/vite/bin/vite.js", "--host", host, "--port", String(frontendPort)],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  vite.stdout.on("data", (chunk) => {
    viteLog += chunk.toString();
  });
  vite.stderr.on("data", (chunk) => {
    viteLog += chunk.toString();
  });
  await waitForUrl(frontendUrl, "Vite", () => viteLog);

  browser = await chromium.launch({ headless: true });
  page = await browser.newPage();
  await page.goto(frontendUrl);

  // Fresh installation: first administrator is created entirely in the browser.
  await page.getByRole("heading", { name: "Create your administrator account", exact: true }).waitFor();
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password", { exact: true }).fill(password);
  await (await waitForButton(page, "Create administrator")).click();

  await page.waitForURL("**/onboarding");
  await page.getByRole("heading", { name: "Guided onboarding", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Model setup", exact: true }).waitFor();
  await assertModelSetupContract(page);

  await page.locator('select[name="adapter_id"]').selectOption("openai-compatible");
  await page.locator('select[name="location"]').selectOption("local");
  await page.getByLabel("Provider ID", { exact: true }).fill("browser-local-provider");
  await page.getByLabel("Model configuration ID", { exact: true }).fill("browser-local-model");
  await page.getByLabel("Provider-native model name", { exact: true }).fill("browser-model-native");
  await page.getByLabel("Display name", { exact: true }).fill("Browser local model");
  await page.getByLabel("Base URL", { exact: true }).fill(`http://${host}:${modelPort}/v1`);
  await page.getByLabel("Context window", { exact: true }).fill("32768");
  await page.getByLabel("Structured output", { exact: true }).check();
  await (await waitForButton(page, "Validate and save model")).click();

  await page.getByRole("heading", { name: "Create project", exact: true }).waitFor();
  await (await waitForButton(page, "Use Recommended / Auto")).click();
  await page.getByRole("status").filter({ hasText: "profile selected and persisted" }).waitFor();

  // Authenticated incomplete setup survives a normal browser reload.
  await page.reload();
  await page.getByRole("heading", { name: "Guided onboarding", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Create project", exact: true }).waitFor();

  // Existing installation with incomplete setup: normal sign-in resumes the setup wizard.
  await logoutThroughBrowserClient(page);
  await page.reload();
  await signIn(page);
  await page.getByRole("heading", { name: "Guided onboarding", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Create project", exact: true }).waitFor();

  await page.getByLabel("Project name", { exact: true }).fill("Browser first-run project");
  const projectResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/projects") && response.request().method() === "POST",
  );
  await (await waitForButton(page, "Create project")).click();
  const projectResponse = await projectResponsePromise;
  if (!projectResponse.ok()) {
    throw new Error(
      `Project creation failed with ${projectResponse.status()}: ${await projectResponse.text()}`,
    );
  }
  const createdProject = await projectResponse.json();
  requireCanonicalIdentity(
    await readPublicApiResource(page, `/projects/${createdProject.id}`),
    createdProject.id,
    "Browser-created Project through public API",
  );
  await page.getByRole("heading", { name: "Create workspace", exact: true }).waitFor();

  const workspaceResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/workspaces") && response.request().method() === "POST",
  );
  await (await waitForButton(page, "Create workspace")).click();
  const workspaceResponse = await workspaceResponsePromise;
  if (!workspaceResponse.ok()) {
    throw new Error(
      `Workspace creation failed with ${workspaceResponse.status()}: ${await workspaceResponse.text()}`,
    );
  }
  const createdWorkspace = await workspaceResponse.json();
  const workspaceViaApi = await readPublicApiResource(page, `/workspaces/${createdWorkspace.id}`);
  requireCanonicalIdentity(
    workspaceViaApi,
    createdWorkspace.id,
    "Browser-created Workspace through public API",
  );
  if (workspaceViaApi.project_id !== createdProject.id) {
    throw new Error(
      `Browser-created Workspace changed Project identity across the public API: ${JSON.stringify(workspaceViaApi)}`,
    );
  }
  await page.getByRole("status").filter({ hasText: "Workspace created" }).waitFor();
  const afterWorkspace = await onboardingStatusSnapshot(page);
  if (afterWorkspace.statusCode !== 200 || afterWorkspace.body.state !== "needs_general_assistant") {
    throw new Error(
      `Unexpected onboarding state after workspace creation: ${JSON.stringify(afterWorkspace)}`,
    );
  }

  // #1164: exercise an unusable local provider through the maintained browser/API boundary.
  // The stale rendered action must fail closed before any canonical Task is created.
  await page.getByRole("heading", { name: "Official first run: multi-agent goal", exact: true }).waitFor();
  const tasksBeforeFailure = await taskInventory(page);
  await closeServer(modelServer);
  const unavailableProvider = await refreshProviderHealthThroughBrowserClient(
    page,
    "browser-local-provider",
  );
  if (unavailableProvider.health === "healthy") {
    throw new Error(`Provider remained healthy while its local endpoint was stopped: ${JSON.stringify(unavailableProvider)}`);
  }

  const failedRunButton = await waitForButton(page, "Run official multi-agent first run");
  await failedRunButton.focus();
  if (!(await failedRunButton.evaluate((element) => document.activeElement === element))) {
    throw new Error("Official first-run action did not accept keyboard focus");
  }
  await failedRunButton.press("Enter");
  await page.getByRole("alert").waitFor();
  const failureText = await page.getByRole("alert").innerText();
  requireText(failureText, "local/self-hosted", "Actionable missing-model failure");
  if (
    failureText.includes(password)
    || failureText.includes(dataDir)
    || failureText.includes("Traceback")
    || failureText.includes("exception_type")
  ) {
    throw new Error(`Browser-visible failure leaked private diagnostics: ${failureText}`);
  }
  const tasksAfterFailure = await taskInventory(page);
  if (JSON.stringify(tasksAfterFailure) !== JSON.stringify(tasksBeforeFailure)) {
    throw new Error(
      `Failed first-run preflight created canonical Task state: before=${JSON.stringify(tasksBeforeFailure)} after=${JSON.stringify(tasksAfterFailure)}`,
    );
  }

  // Recover through the maintained onboarding UI, then execute the real multi-agent workflow.
  // Re-validating and saving replaces the runtime provider attachment with a freshly healthy
  // instance while preserving the canonical model configuration identity.
  await listen(modelServer, modelPort);
  await page.reload();
  await page.getByRole("heading", { name: "Model setup", exact: true }).waitFor();
  await page.locator('select[name="adapter_id"]').selectOption("openai-compatible");
  await page.locator('select[name="location"]').selectOption("local");
  await page.getByLabel("Provider ID", { exact: true }).fill("browser-local-provider");
  await page.getByLabel("Model configuration ID", { exact: true }).fill("browser-local-model");
  await page.getByLabel("Provider-native model name", { exact: true }).fill("browser-model-native");
  await page.getByLabel("Display name", { exact: true }).fill("Browser local model");
  await page.getByLabel("Base URL", { exact: true }).fill(`http://${host}:${modelPort}/v1`);
  await page.getByLabel("Context window", { exact: true }).fill("32768");
  await page.getByLabel("Structured output", { exact: true }).check();
  await (await waitForButton(page, "Validate and save model")).click();
  await page.getByRole("heading", { name: "Official first run: multi-agent goal", exact: true }).waitFor();

  const successfulRunButton = await waitForButton(page, "Run official multi-agent first run");
  const commandResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/commands/onboarding.run-multi-agent-golden-path")
      && response.request().method() === "POST",
  );
  await successfulRunButton.focus();
  await successfulRunButton.press("Enter");
  await page.getByRole("button", { name: "Running multi-agent goal…", exact: true }).waitFor();
  const commandResponse = await commandResponsePromise;
  if (!commandResponse.ok()) {
    throw new Error(
      `Official browser multi-agent first run failed with ${commandResponse.status()}: ${await commandResponse.text()}`,
    );
  }
  const commandRequest = commandResponse.request();
  if (!commandRequest.headers()["idempotency-key"]) {
    throw new Error("Official browser first-run mutation omitted its public idempotency key");
  }
  const firstRunResult = await commandResponse.json();

  // #1236: the browser result is only a projection. Every emitted identity must resolve through
  // the public Control Plane and agree with the server-owned lifecycle state.
  const taskViaApi = await readPublicApiResource(page, `/tasks/${firstRunResult.task_id}`);
  requireCanonicalIdentity(taskViaApi, firstRunResult.task_id, "First-run Task");
  if (
    taskViaApi.status !== firstRunResult.task_status
    || taskViaApi.plan_ref !== firstRunResult.plan_id
    || taskViaApi.project_id !== firstRunResult.project_id
  ) {
    throw new Error(
      `Browser/API Task state diverged after first run: browser=${JSON.stringify(firstRunResult)} api=${JSON.stringify(taskViaApi)}`,
    );
  }

  const planViaApi = await readPublicApiResource(page, `/plans/${firstRunResult.plan_id}`);
  requireCanonicalIdentity(planViaApi, firstRunResult.plan_id, "First-run Plan");
  if (planViaApi.task_id !== firstRunResult.task_id) {
    throw new Error(`First-run Plan resolved to a different Task: ${JSON.stringify(planViaApi)}`);
  }

  for (const step of firstRunResult.steps) {
    requireCanonicalIdentity(
      await readPublicApiResource(page, `/steps/${step.step_id}`),
      step.step_id,
      "First-run Step",
    );
    if (step.run_id) {
      const runViaApi = await readPublicApiResource(page, `/runs/${step.run_id}`);
      requireCanonicalIdentity(runViaApi, step.run_id, "First-run Run");
      if (runViaApi.task_id !== firstRunResult.task_id || runViaApi.status !== step.run_status) {
        throw new Error(
          `Browser/API Run state diverged for ${step.run_id}: browser=${JSON.stringify(step)} api=${JSON.stringify(runViaApi)}`,
        );
      }
    }
  }

  for (const [role, agent] of Object.entries(firstRunResult.agents)) {
    const agentViaApi = await readPublicApiResource(page, `/agents/${agent.agent_id}`);
    requireCanonicalIdentity(agentViaApi, agent.agent_id, `First-run ${role} Agent`);
  }
  for (const resultId of firstRunResult.result_ids) {
    const resultViaApi = await readPublicApiResource(page, `/results/${resultId}`);
    requireCanonicalIdentity(resultViaApi, resultId, "First-run Result");
    if (resultViaApi.task_id !== firstRunResult.task_id) {
      throw new Error(`First-run Result resolved to a different Task: ${JSON.stringify(resultViaApi)}`);
    }
  }
  for (const artifactId of firstRunResult.artifact_ids) {
    const artifactViaApi = await readPublicApiResource(page, `/artifacts/${artifactId}`);
    requireCanonicalIdentity(artifactViaApi, artifactId, "First-run Artifact");
    if (artifactViaApi.task_id !== firstRunResult.task_id) {
      throw new Error(`First-run Artifact resolved to a different Task: ${JSON.stringify(artifactViaApi)}`);
    }
  }
  for (const verification of firstRunResult.verification) {
    const verificationViaApi = await readPublicApiResource(
      page,
      `/verifications/${verification.verification_id}`,
    );
    requireCanonicalIdentity(
      verificationViaApi,
      verification.verification_id,
      "First-run Verification",
    );
    if (
      verificationViaApi.task_id !== firstRunResult.task_id
      || verificationViaApi.status !== verification.status
    ) {
      throw new Error(
        `Browser/API Verification state diverged: browser=${JSON.stringify(verification)} api=${JSON.stringify(verificationViaApi)}`,
      );
    }
  }

  const resultCard = page.getByRole("heading", {
    name: "Official multi-agent first-run result",
    exact: true,
  }).locator("..");
  await resultCard.waitFor();
  const resultText = await resultCard.innerText();
  for (const expected of [
    "Task",
    "succeeded",
    "Plan steps",
    "4",
    "Specialized roles",
    "3",
    "Verification",
    "pass",
    "Artifacts",
    "researcher",
    "developer",
    "reviewer",
    "Canonical verification",
    "produced result",
  ]) {
    requireText(resultText.toLowerCase(), expected.toLowerCase(), "Official multi-agent browser result");
  }
  const taskLink = resultCard.getByRole("link", { name: "Open Task", exact: true });
  await taskLink.waitFor();
  if ((await taskLink.getAttribute("href")) !== `/tasks/${firstRunResult.task_id}`) {
    throw new Error("Official browser result did not deep-link the canonical Task ID");
  }
  const producedResultLink = resultCard.getByRole("link", { name: "Open produced Result", exact: true });
  await producedResultLink.waitFor();
  if ((await resultCard.getByRole("link", { name: "Run", exact: true }).count()) < 4) {
    throw new Error("Official browser result did not expose all canonical Step runs");
  }
  if ((await resultCard.locator('a[href^="/artifacts/"]').count()) < 1) {
    throw new Error("Official browser result did not expose a canonical Artifact link");
  }
  if (chatCompletionCount < 5) {
    throw new Error(`Official browser first run did not execute the real local model path: ${chatCompletionCount} completion calls`);
  }
  const tasksAfterSuccess = await taskInventory(page);
  if (tasksAfterSuccess.length !== tasksBeforeFailure.length + 1) {
    throw new Error(
      `Official browser first run created an unexpected number of canonical Tasks: before=${JSON.stringify(tasksBeforeFailure)} after=${JSON.stringify(tasksAfterSuccess)}`,
    );
  }
  const producedResultHref = await producedResultLink.getAttribute("href");
  if (producedResultHref !== `/results/${firstRunResult.result_id}`) {
    throw new Error(`Produced Result link did not expose the canonical Result route: ${producedResultHref}`);
  }
  const runHrefs = await resultCard.locator('a[href^="/runs/"]').evaluateAll(
    (links) => links.map((link) => link.getAttribute("href")).filter(Boolean),
  );
  const expectedRunHrefs = firstRunResult.steps
    .filter((step) => step.run_id)
    .map((step) => `/runs/${step.run_id}`)
    .sort();
  if (JSON.stringify([...runHrefs].sort()) !== JSON.stringify(expectedRunHrefs)) {
    throw new Error(
      `Browser Run deep links diverged from canonical IDs: browser=${JSON.stringify(runHrefs)} expected=${JSON.stringify(expectedRunHrefs)}`,
    );
  }
  const artifactHrefs = await resultCard.locator('a[href^="/artifacts/"]').evaluateAll(
    (links) => links.map((link) => link.getAttribute("href")).filter(Boolean).sort(),
  );
  const expectedArtifactHrefs = firstRunResult.artifact_ids
    .map((artifactId) => `/artifacts/${artifactId}`)
    .sort();
  if (JSON.stringify(artifactHrefs) !== JSON.stringify(expectedArtifactHrefs)) {
    throw new Error(
      `Browser Artifact deep links diverged from canonical IDs: browser=${JSON.stringify(artifactHrefs)} expected=${JSON.stringify(expectedArtifactHrefs)}`,
    );
  }
  const createdTask = tasksAfterSuccess.find(
    (task) => !tasksBeforeFailure.some((before) => before.id === task.id),
  );
  if (!createdTask) {
    throw new Error("Official browser first run did not expose the newly created canonical Task");
  }

  await page.getByRole("heading", { name: "Optional General Assistant setup", exact: true }).waitFor();
  const standardBootstrapResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/commands/standard-agent.bootstrap")
      && response.request().method() === "POST",
  );
  await (await waitForButton(page, "Bootstrap standard Agents")).click();
  const standardBootstrapResponse = await standardBootstrapResponsePromise;
  if (!standardBootstrapResponse.ok()) {
    throw new Error(
      `Standard Agent/Team bootstrap failed with ${standardBootstrapResponse.status()}: ${await standardBootstrapResponse.text()}`,
    );
  }
  const standardBootstrapResult = await standardBootstrapResponse.json();
  const expectedTeamKeys = [
    ...(standardBootstrapResult.installed_team_keys ?? []),
    ...(standardBootstrapResult.preserved_team_keys ?? []),
  ].sort();
  if (expectedTeamKeys.length === 0) {
    throw new Error(
      `Standard bootstrap exposed no canonical Agent Team keys: ${JSON.stringify(standardBootstrapResult)}`,
    );
  }
  const standardTeams = await standardAgentTeamInventory(page);
  for (const teamKey of expectedTeamKeys) {
    const team = standardTeams.find(
      (candidate) => candidate.starterKey === teamKey && candidate.starterKind === "team",
    );
    if (!team) {
      throw new Error(
        `Standard Agent Team ${teamKey} was not visible through the public agent-teams collection: ${JSON.stringify(standardTeams)}`,
      );
    }
    const teamViaApi = await readPublicApiResource(page, `/agent-teams/${team.id}`);
    requireCanonicalIdentity(teamViaApi, team.id, `Standard Agent Team ${teamKey}`);
    if (
      teamViaApi.revision?.profile?.metadata?.starter_key !== teamKey
      || teamViaApi.revision?.profile?.metadata?.starter_kind !== "team"
    ) {
      throw new Error(
        `Standard Agent Team metadata diverged through the public API: ${JSON.stringify(teamViaApi)}`,
      );
    }
  }
  await (await waitForButton(page, "Create editable General Assistant")).click();

  await page.getByRole("heading", { name: "Optional single-Agent first task", exact: true }).waitFor();
  await (await waitForButton(page, "Refresh setup state")).click();
  await (await waitForButton(page, "Validate readiness")).click();
  await page.getByRole("link", { name: "Open dashboard", exact: true }).waitFor();

  // Incomplete installations intentionally redirect non-onboarding routes back here.
  // Exercise terminal Result keyboard navigation only after the canonical setup gate is ready.
  const readyResultCard = page.getByRole("heading", {
    name: "Official multi-agent first-run result",
    exact: true,
  }).locator("..");
  const readyProducedResultLink = readyResultCard.getByRole("link", {
    name: "Open produced Result",
    exact: true,
  });
  if ((await readyProducedResultLink.getAttribute("href")) !== producedResultHref) {
    throw new Error("Produced Result route changed while completing setup readiness");
  }
  await readyProducedResultLink.focus();
  if (!(await readyProducedResultLink.evaluate((element) => document.activeElement === element))) {
    throw new Error("Produced Result navigation did not accept keyboard focus");
  }
  const resultNavigation = page.waitForURL(`${frontendUrl}${producedResultHref}`);
  await page.keyboard.press("Enter");
  await resultNavigation;
  await page.getByRole("heading", { name: "Result reference", exact: true }).waitFor();

  await page.goto(`${frontendUrl}/onboarding`);
  await page.getByRole("heading", { name: "Guided onboarding", exact: true }).waitFor();
  await page.getByRole("link", { name: "Open dashboard", exact: true }).waitFor();
  await page.getByRole("link", { name: "Open dashboard", exact: true }).click();

  await page.waitForURL(`${frontendUrl}/`);
  await page.getByRole("heading", { name: "Platform overview", exact: true }).waitFor();

  // Completed setup survives reload and no longer routes back to onboarding.
  await page.reload();
  await page.getByRole("heading", { name: "Platform overview", exact: true }).waitFor();

  // #1236 reverse direction: mutate canonical state through the public API, then prove the
  // maintained Web detail route observes that server-owned state after a real reload.
  const apiCreatedTask = await publicApiCommand(page, "/tasks", {
    title: "API-to-Web refresh parity",
    objective: "Prove the browser reload cannot override newer canonical server state.",
    owner_type: taskViaApi.owner.type,
    owner_id: taskViaApi.owner.id,
    project_id: firstRunResult.project_id,
  });
  requireCanonicalIdentity(apiCreatedTask, apiCreatedTask.id, "API-created Task");
  if (apiCreatedTask.status !== "draft") {
    throw new Error(`API-created parity Task did not start as draft: ${JSON.stringify(apiCreatedTask)}`);
  }

  await page.goto(`${frontendUrl}/tasks/${apiCreatedTask.id}`);
  await page.getByRole("heading", { name: "API-to-Web refresh parity", exact: true }).waitFor();
  await page.locator(".detail-status .status").filter({ hasText: "draft" }).waitFor();

  const cancelledViaApi = await publicApiCommand(
    page,
    `/tasks/${apiCreatedTask.id}:cancel`,
    undefined,
  );
  requireCanonicalIdentity(cancelledViaApi, apiCreatedTask.id, "API-cancelled Task");
  if (cancelledViaApi.status !== "cancelled") {
    throw new Error(`Public API cancellation did not return canonical cancelled state: ${JSON.stringify(cancelledViaApi)}`);
  }

  await page.reload();
  await page.getByRole("heading", { name: "API-to-Web refresh parity", exact: true }).waitFor();
  await page.locator(".detail-status .status").filter({ hasText: "cancelled" }).waitFor();
  const renderedRevision = await page
    .locator("dt", { hasText: "revision" })
    .locator("xpath=following-sibling::dd[1]")
    .innerText();
  if (renderedRevision.trim() !== String(cancelledViaApi.revision)) {
    throw new Error(
      `Reloaded Web revision ${renderedRevision} did not match canonical revision ${cancelledViaApi.revision}`,
    );
  }
  const refreshedViaApi = await readPublicApiResource(page, `/tasks/${apiCreatedTask.id}`);
  if (refreshedViaApi.status !== "cancelled" || refreshedViaApi.revision !== cancelledViaApi.revision) {
    throw new Error(
      `Reloaded Web state did not agree with the latest canonical Task revision: web/API=${JSON.stringify(refreshedViaApi)} mutation=${JSON.stringify(cancelledViaApi)}`,
    );
  }

  const bootstrapStatus = await page.evaluate(async () => {
    const response = await fetch("/api/v1/auth/bootstrap-status");
    return response.json();
  });
  if (bootstrapStatus.state !== "initialized" || bootstrapStatus.bootstrap_available !== false) {
    throw new Error(`Bootstrap endpoint did not fail closed after first run: ${JSON.stringify(bootstrapStatus)}`);
  }

  // Existing completed installation: ordinary sign-in returns directly to the dashboard.
  await page.goto(`${frontendUrl}/settings`);
  await page.getByRole("heading", { name: "Settings", exact: true }).waitFor();
  await (await waitForButton(page, "Sign out")).click();
  await page.goto(frontendUrl);
  await signIn(page);
  await page.getByRole("heading", { name: "Platform overview", exact: true }).waitFor();

  // #1234 final owner-domain closure: both previously missing V1 inspection surfaces must be
  // reachable through the maintained authenticated browser shell without private API calls.
  await page.goto(`${frontendUrl}/research`);
  await page.getByRole("heading", { name: "Research Evidence", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Research Items", exact: true }).waitFor();

  await page.goto(`${frontendUrl}/decisions`);
  await page.getByRole("heading", { name: "Decision Records", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Decision history", exact: true }).waitFor();

  // Keep representative cross-domain navigation in the maintained browser-first-run harness.
  // Historical context: #1234 consumes #1164 rather than creating a competing E2E architecture.
  // Deep-linked canonical detail state must survive reload, Search filters must
  // survive reload/back navigation, and an unknown URL must remain distinct from an optional
  // provider/resource-unavailable state.
  const taskPath = `/tasks/${encodeURIComponent(createdTask.id)}`;
  await page.goto(`${frontendUrl}${taskPath}`);
  await page.locator(`main[data-route="${taskPath}"]`).waitFor();
  await page.locator(`code[title="${createdTask.id}"]`).first().waitFor();
  await page.reload();
  await page.locator(`main[data-route="${taskPath}"]`).waitFor();
  await page.locator(`code[title="${createdTask.id}"]`).first().waitFor();

  const searchPath = "/search?q=Browser&types=task";
  await page.goto(`${frontendUrl}${searchPath}`);
  await page.getByRole("heading", { name: "Global search", exact: true }).waitFor();
  if ((await page.locator('input[name="q"]').inputValue()) !== "Browser") {
    throw new Error("Global Search query was not restored from the deep-link URL");
  }
  if ((await page.locator('input[name="types"]').inputValue()) !== "task") {
    throw new Error("Global Search type filter was not restored from the deep-link URL");
  }
  await page.reload();
  await page.getByRole("heading", { name: "Global search", exact: true }).waitFor();
  if ((await page.locator('input[name="q"]').inputValue()) !== "Browser") {
    throw new Error("Global Search query did not survive browser reload");
  }

  await page.goto(`${frontendUrl}/definitely-missing-route`);
  await page.getByRole("heading", { name: "Page not found", exact: true }).waitFor();
  await page.getByRole("link", { name: "Return to platform overview", exact: true }).waitFor();
  await page.goBack();
  await page.waitForURL(`${frontendUrl}${searchPath}`);
  if ((await page.locator('input[name="q"]').inputValue()) !== "Browser") {
    throw new Error("Global Search query did not survive browser Back navigation");
  }

  // A direct deep link with revoked/insufficient permission must fail closed as Access denied,
  // not as an empty resource or a retryable provider outage.
  const taskDetailApi = `**/api/v1/tasks/${createdTask.id}`;
  const denyTaskDetail = async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({
        code: "forbidden",
        category: "authorization",
        message: "browser acceptance permission denial",
        request_id: "request_browser_permission_denied",
        correlation_id: "correlation_browser_permission_denied",
        retryable: false,
      }),
    });
  };
  await page.route(taskDetailApi, denyTaskDetail);
  await page.goto(`${frontendUrl}${taskPath}`);
  const deniedAlert = page.getByRole("alert").filter({ hasText: "Access denied" });
  await deniedAlert.waitFor();
  if ((await deniedAlert.getByRole("button", { name: "Retry", exact: true }).count()) !== 0) {
    throw new Error("Non-retryable permission denial exposed a Retry action");
  }
  await page.unroute(taskDetailApi, denyTaskDetail);

  // A resource that disappeared behind a once-valid deep link must be an explicit Not found state.
  const missingTaskDetail = async (route) => {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({
        code: "not_found",
        category: "request",
        message: "browser acceptance resource missing",
        request_id: "request_browser_not_found",
        correlation_id: "correlation_browser_not_found",
        retryable: false,
      }),
    });
  };
  await page.route(taskDetailApi, missingTaskDetail);
  await page.reload();
  const missingAlert = page.getByRole("alert").filter({ hasText: "Not found" });
  await missingAlert.waitFor();
  if ((await missingAlert.getByRole("button", { name: "Retry", exact: true }).count()) !== 0) {
    throw new Error("Non-retryable missing-resource state exposed a Retry action");
  }
  await page.unroute(taskDetailApi, missingTaskDetail);
  await page.reload();
  await page.locator(`main[data-route="${taskPath}"]`).waitFor();
  await page.locator(`code[title="${createdTask.id}"]`).first().waitFor();

  // Force both transport attempts for manifest discovery to fail once. The maintained shell must
  // expose a retryable Control Plane outage and recover in-place when the next manifest read works.
  let manifestFailures = 0;
  const manifestApi = "**/api/v1/";
  const failManifestTemporarily = async (route) => {
    if (manifestFailures < 2) {
      manifestFailures += 1;
      await route.abort("connectionfailed");
      return;
    }
    await route.continue();
  };
  await page.route(manifestApi, failManifestTemporarily);
  await page.goto(`${frontendUrl}/tools`);
  const manifestAlert = page.getByRole("alert").filter({ hasText: "Control Plane unavailable" });
  await manifestAlert.waitFor();
  await manifestAlert.getByRole("button", { name: "Retry", exact: true }).click();
  await page.locator(".api-indicator").filter({ hasText: "/api/v1" }).waitFor();
  await manifestAlert.waitFor({ state: "detached" });
  await page.unroute(manifestApi, failManifestTemporarily);

  // #1296: close the final #747 representative browser-evidence gap through
  // operator diagnostics/status. Re-enter the already-proven Search/history
  // surface, use the maintained shell navigation, and keep every read on the
  // ordinary public Control Plane boundary.
  await page.goto(`${frontendUrl}${searchPath}`);
  await page.getByRole("heading", { name: "Global search", exact: true }).waitFor();
  const observabilityLink = page.getByRole("link", { name: "Observability", exact: true });
  await observabilityLink.waitFor();
  await observabilityLink.click();
  await page.waitForURL(`${frontendUrl}/observability`);
  await page.getByRole("heading", { name: "Observability", exact: true }).waitFor();

  const firstRunRunId = firstRunResult.steps.find((step) => step.run_id)?.run_id;
  if (!firstRunRunId) {
    throw new Error("Official multi-agent first run exposed no canonical Run for diagnostics correlation");
  }

  const timelineResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes(
        `/api/v1/tasks/${encodeURIComponent(firstRunResult.task_id)}/timeline`,
      )
      && response.request().method() === "GET",
  );
  await page.getByLabel("Exact Task ID", { exact: true }).fill(firstRunResult.task_id);
  await (await waitForButton(page, "Open telemetry")).click();
  const timelineResponse = await timelineResponsePromise;
  if (!timelineResponse.ok()) {
    throw new Error(
      `Observability timeline failed with ${timelineResponse.status()}: ${await timelineResponse.text()}`,
    );
  }
  const timelinePayload = await timelineResponse.json();
  if (
    !timelinePayload.items?.some(
      (item) =>
        item.type === "event"
        && item.subject_type === "run"
        && item.subject_id === firstRunRunId,
    )
  ) {
    throw new Error(
      `Public task timeline did not retain canonical Run ${firstRunRunId}: ${JSON.stringify(timelinePayload)}`,
    );
  }

  const observabilityRoute = page.locator('main[data-route="/observability"]');
  await observabilityRoute.locator(`code[title="${firstRunResult.task_id}"]`).first().waitFor();
  const timelineHeading = page.getByRole("heading", { name: "Timeline", exact: true });
  await timelineHeading.waitFor();
  const timelineCard = timelineHeading.locator("..");
  await timelineCard.locator("tbody tr").first().waitFor();
  await timelineCard.getByText(`run:${firstRunRunId}`, { exact: true }).first().waitFor();
  if ((await timelineCard.getByText("No timeline entries", { exact: true }).count()) !== 0) {
    throw new Error("Observability rendered its empty state for the completed first-run Task");
  }

  // Finish on the canonical operator status surface and distinguish normal
  // readiness from a degraded/empty presentation.
  await page.goto(frontendUrl);
  await page.getByRole("heading", { name: "Platform overview", exact: true }).waitFor();
  const readinessMetric = page.locator(".metric").filter({ hasText: "Readiness" });
  await readinessMetric.waitFor();
  const readinessText = (await readinessMetric.innerText()).toLowerCase();
  if (!readinessText.includes("ready") || readinessText.includes("degraded")) {
    throw new Error(`Platform overview did not report ready status: ${readinessText}`);
  }
  const publicHealth = await readPublicApiResource(page, "/health");
  if (publicHealth.ready !== true || publicHealth.readiness_state !== "ready") {
    throw new Error(
      `Public Control Plane health was not normally ready: ${JSON.stringify(publicHealth)}`,
    );
  }
} catch (error) {
  await mkdir(artifactDir, { recursive: true }).catch(() => undefined);
  if (page) {
    await page.screenshot({
      path: join(artifactDir, "browser-first-run-failure.png"),
      fullPage: true,
    }).catch(() => undefined);
    await writeFile(
      join(artifactDir, "browser-first-run-page.txt"),
      await page.locator("body").innerText().catch(() => "page body unavailable"),
      "utf8",
    ).catch(() => undefined);
  }
  await writeFile(
    join(artifactDir, "browser-first-run-process.log"),
    `--- platform-server ---\n${backendLog}\n--- vite ---\n${viteLog}`,
    "utf8",
  ).catch(() => undefined);
  throw new Error(
    `${error instanceof Error ? error.stack ?? error.message : String(error)}\n\n`
    + `--- platform-server ---\n${backendLog}\n`
    + `--- vite ---\n${viteLog}`,
  );
} finally {
  if (browser) await browser.close();
  if (vite) await stopProcess(vite, "Vite");
  if (backend) await stopProcess(backend, "Single-node backend");
  await closeServer(modelServer).catch(() => undefined);
  await rm(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
