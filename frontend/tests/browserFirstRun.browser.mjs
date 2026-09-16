import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
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

let backend;
let vite;
let browser;
let backendLog = "";
let viteLog = "";

const modelServer = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/v1/models") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ data: [{ id: "browser-model-native" }] }));
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
  const page = await browser.newPage();
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

  await page.getByLabel("Installed adapter", { exact: true }).selectOption("openai-compatible");
  await page.getByLabel("Location", { exact: true }).selectOption("local");
  await page.getByLabel("Provider ID", { exact: true }).fill("browser-local-provider");
  await page.getByLabel("Model configuration ID", { exact: true }).fill("browser-local-model");
  await page.getByLabel("Provider-native model name", { exact: true }).fill("browser-model-native");
  await page.getByLabel("Display name", { exact: true }).fill("Browser local model");
  await page.getByLabel("Base URL", { exact: true }).fill(`http://${host}:${modelPort}/v1`);
  await (await waitForButton(page, "Validate and save model")).click();

  await page.getByRole("heading", { name: "Create project", exact: true }).waitFor();
  await (await waitForButton(page, "Use Recommended / Auto")).click();
  await page.getByRole("status").filter({ hasText: "profile selected and persisted" }).waitFor();

  // Existing installation with incomplete setup: normal sign-in resumes the setup wizard.
  await logoutThroughBrowserClient(page);
  await page.reload();
  await signIn(page);
  await page.getByRole("heading", { name: "Guided onboarding", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Create project", exact: true }).waitFor();

  await page.getByLabel("Project name", { exact: true }).fill("Browser first-run project");
  await (await waitForButton(page, "Create project")).click();
  await page.getByRole("heading", { name: "Create workspace", exact: true }).waitFor();
  await (await waitForButton(page, "Create workspace")).click();

  await page.getByRole("heading", { name: "Optional General Assistant setup", exact: true }).waitFor();
  await (await waitForButton(page, "Bootstrap standard Agents")).click();
  await (await waitForButton(page, "Create editable General Assistant")).click();

  await page.getByRole("heading", { name: "Optional single-Agent first task", exact: true }).waitFor();
  await (await waitForButton(page, "Refresh setup state")).click();
  await (await waitForButton(page, "Validate readiness")).click();
  await page.getByRole("link", { name: "Open dashboard", exact: true }).waitFor();
  await page.getByRole("link", { name: "Open dashboard", exact: true }).click();

  await page.waitForURL(`${frontendUrl}/`);
  await page.getByRole("heading", { name: "Platform overview", exact: true }).waitFor();

  // Completed setup survives reload and no longer routes back to onboarding.
  await page.reload();
  await page.getByRole("heading", { name: "Platform overview", exact: true }).waitFor();

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
} catch (error) {
  throw new Error(
    `${error instanceof Error ? error.stack ?? error.message : String(error)}\n\n`
    + `--- platform-server ---\n${backendLog}\n`
    + `--- vite ---\n${viteLog}`,
  );
} finally {
  if (browser) await browser.close();
  if (vite) vite.kill("SIGTERM");
  if (backend) backend.kill("SIGTERM");
  await closeServer(modelServer).catch(() => undefined);
  await rm(dataDir, { recursive: true, force: true });
}
