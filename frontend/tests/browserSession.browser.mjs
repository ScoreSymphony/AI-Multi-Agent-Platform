import { spawn } from "node:child_process";
import { chromium } from "playwright";

const host = "127.0.0.1";
const port = 4173;
const baseUrl = `http://${host}:${port}`;
const vite = spawn(
  process.execPath,
  ["node_modules/vite/bin/vite.js", "--host", host, "--port", String(port)],
  { stdio: ["ignore", "pipe", "pipe"] },
);

let stderr = "";
vite.stderr.on("data", (chunk) => {
  stderr += chunk.toString();
});

async function waitForVite() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Vite did not start. ${stderr}`);
}

function requireText(haystack, needle, label) {
  if (!haystack.includes(needle)) {
    throw new Error(`${label} missing ${JSON.stringify(needle)} from ${JSON.stringify(haystack)}`);
  }
}

function cardByHeading(page, name) {
  return page.getByRole("heading", { name, exact: true }).locator("..");
}

let browser;
try {
  await waitForVite();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  await page.goto(baseUrl);
  const result = await page.evaluate(async () => {
    const { BrowserSessionClient } = await import("/src/api/browserSession.ts");
    const session = new BrowserSessionClient({ storage: null });
    const response = await session.fetch(
      "data:application/json,%7B%22browserFetchBound%22%3Atrue%7D",
    );
    return response.json();
  });
  if (result.browserFetchBound !== true) {
    throw new Error(`Unexpected BrowserSessionClient response: ${JSON.stringify(result)}`);
  }

  await page.goto(`${baseUrl}/tests/marketplaceHarness.html`);
  await page.getByRole("heading", { name: "ProjectAtlas", exact: true }).waitFor();

  const initialMarketplaceText = await page.locator("body").innerText();
  for (const expected of [
    "ProjectAtlas",
    "Code Review Skill",
    "Example Plugin",
    "GitHub Connector",
    "Code Server",
    "Skills",
    "Applications",
    "Templates / Workflows",
  ]) {
    requireText(initialMarketplaceText, expected, "Unified Marketplace");
  }

  const initialCalls = await page.evaluate(() => [...window.__marketplaceCalls]);
  const initialListCall = initialCalls.find(
    (call) => call.method === "GET" && call.url.includes("/api/v1/registry-items"),
  );
  if (!initialListCall) {
    throw new Error(`Marketplace did not query registry-items: ${JSON.stringify(initialCalls)}`);
  }
  const decodedInitialUrl = decodeURIComponent(initialListCall.url);
  if (decodedInitialUrl.includes("filter[technical_component]=true")) {
    throw new Error(`Unified Marketplace unexpectedly defaults to technical-only: ${decodedInitialUrl}`);
  }
  requireText(decodedInitialUrl, "sort=name", "Marketplace server-side sorting");
  requireText(decodedInitialUrl, "direction=asc", "Marketplace server-side sorting");

  let before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("filter[item_type]=skill"),
      ),
    before,
  );
  const skillCard = cardByHeading(page, "Code Review Skill");
  await skillCard.getByRole("button", { name: "Inspect", exact: true }).click();
  await page.getByRole("button", { name: "Preview install", exact: true }).waitFor();
  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByRole("button", { name: "Preview install", exact: true }).click();
  await page.getByRole("heading", { name: "Install / update preview", exact: true }).waitFor();
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some(
        (call) => call.method === "POST" && call.url.endsWith("/commands/marketplace.preview"),
      ),
    before,
  );
  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByRole("button", { name: "Install component", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some(
        (call) => call.method === "POST" && call.url.endsWith("/commands/marketplace.install"),
      ),
    before,
  );
  await page.getByRole("status").filter({ hasText: "Component installed." }).waitFor();

  await page.getByRole("button", { name: "All", exact: true }).click();
  const searchInput = page.getByRole("searchbox", { name: "Search" });
  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await searchInput.fill("GitHub Connector");
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("q=GitHub+Connector") ||
        decodeURIComponent(call.url).includes("q=GitHub%20Connector") ||
        decodeURIComponent(call.url).includes("q=GitHub Connector"),
      ),
    before,
  );
  await page.getByRole("heading", { name: "GitHub Connector", exact: true }).waitFor();

  await searchInput.fill("");
  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByLabel("Sort").selectOption("publisher");
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("sort=publisher"),
      ),
    before,
  );
  await page.getByLabel("Sort").selectOption("name");

  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByLabel("Installed state").selectOption("true");
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("filter[installed]=true"),
      ),
    before,
  );
  await page.getByLabel("Installed state").selectOption("");

  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByRole("button", { name: "Templates / Workflows", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("filter[item_type]=template,workflow"),
      ),
    before,
  );
  await page.getByRole("heading", { name: "Release Template", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Release Workflow", exact: true }).waitFor();

  await page.getByRole("button", { name: "All", exact: true }).click();
  const kindInput = page.getByRole("textbox", { name: "Component kind", exact: true });
  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await kindInput.fill("notebook_extension");
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("filter[item_type]=notebook_extension"),
      ),
    before,
  );
  await page.getByRole("heading", { name: "Notebook Extension", exact: true }).waitFor();
  await kindInput.fill("");
  await page.getByRole("heading", { name: "ProjectAtlas", exact: true }).waitFor();
  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("cursor=page-2"),
      ),
    before,
  );
  await page.getByRole("heading", { name: "Notebook Extension", exact: true }).waitFor();
  const secondPageText = await page.locator("body").innerText();
  requireText(secondPageText, "Blocked Tool", "Marketplace incompatible state");
  requireText(secondPageText, "Manual Reference", "Marketplace manual state");
  requireText(secondPageText, "incompatible", "Marketplace incompatible badge");
  requireText(secondPageText, "manual", "Marketplace manual badge");
  requireText(secondPageText, "Notebook Extension", "Future Marketplace kind");

  await page.getByRole("button", { name: "Previous page", exact: true }).click();
  await page.getByRole("heading", { name: "Example Plugin", exact: true }).waitFor();

  const pluginCard = cardByHeading(page, "Example Plugin");
  await pluginCard.getByRole("button", { name: "Inspect", exact: true }).click();
  await page.getByRole("button", { name: "Preview update", exact: true }).waitFor();
  await page.getByRole("button", { name: "Preview update", exact: true }).click();
  await page.getByRole("heading", { name: "Permission changes", exact: true }).waitFor();
  const pluginPreviewText = await page.locator("body").innerText();
  requireText(pluginPreviewText, "filesystem.write", "Marketplace permission diff");
  requireText(pluginPreviewText, "Approval required", "Marketplace approval state");
  requireText(pluginPreviewText, "Permission escalation requires approval", "Marketplace approval reason");
  requireText(pluginPreviewText, "Review filesystem write access", "Marketplace security notice");
  requireText(pluginPreviewText, "Trust or integrity state changed", "Marketplace trust/integrity notice");
  requireText(pluginPreviewText, "Source changed", "Marketplace source diff");
  requireText(pluginPreviewText, "Publisher changed", "Marketplace publisher diff");
  requireText(pluginPreviewText, "Provenance changes", "Marketplace provenance diff");
  requireText(pluginPreviewText, "Artifact digest", "Marketplace integrity diff");
  requireText(pluginPreviewText, "ProjectAtlas", "Marketplace dependency preview");
  requireText(pluginPreviewText, "Operating systems", "Marketplace OS compatibility metadata");
  requireText(pluginPreviewText, "linux", "Marketplace OS compatibility value");
  requireText(pluginPreviewText, "Architectures", "Marketplace architecture metadata");
  requireText(pluginPreviewText, "x86_64", "Marketplace architecture value");
  requireText(pluginPreviewText, "Required runtimes", "Marketplace runtime requirements");
  requireText(pluginPreviewText, "node", "Marketplace runtime requirement value");
  requireText(pluginPreviewText, "Signature changed", "Marketplace signature diff");

  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByRole("button", { name: "Apply update", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some(
        (call) => call.method === "POST" && call.url.endsWith("/commands/marketplace.update"),
      ),
    before,
  );
  await page.getByRole("status").filter({ hasText: "Update applied." }).waitFor();

  await page.getByRole("button", { name: "Next page", exact: true }).click();
  const blockedCard = cardByHeading(page, "Blocked Tool");
  await blockedCard.getByRole("button", { name: "Inspect", exact: true }).click();
  await page.getByRole("button", { name: "Preview install", exact: true }).waitFor();
  await page.getByRole("button", { name: "Preview install", exact: true }).click();
  await page.getByRole("heading", { name: "Install / update preview", exact: true }).waitFor();
  const blockedPreviewText = await page.locator("body").innerText();
  requireText(blockedPreviewText, "Mutation is blocked", "Marketplace blocked preview");
  requireText(blockedPreviewText, "Host requirement is not satisfied", "Marketplace compatibility blocker");
  requireText(blockedPreviewText, "Compatibility policy blocked installation", "Marketplace policy blocker");
  requireText(blockedPreviewText, "runtime: runtime.gpu", "Marketplace missing runtime");
  requireText(blockedPreviewText, "operating system", "Marketplace incompatible host requirement");
  requireText(blockedPreviewText, "Incompatible update", "Marketplace incompatible update state");
  if (await page.getByRole("button", { name: "Install component", exact: true }).count()) {
    throw new Error("Blocked Marketplace preview exposed an install action");
  }

  const futureCard = cardByHeading(page, "Notebook Extension");
  await futureCard.getByRole("button", { name: "Inspect", exact: true }).click();
  const futureDetailText = await page.locator("body").innerText();
  requireText(futureDetailText, "Notebook Extension", "Future Marketplace kind detail");
  requireText(futureDetailText, "Notebook Extension", "Future Marketplace kind fallback label");
  before = await page.evaluate(() => window.__marketplaceCalls.length);
  await page.getByRole("button", { name: "Uninstall", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__marketplaceCalls.slice(count).some(
        (call) => call.method === "POST" && call.url.endsWith("/commands/marketplace.uninstall"),
      ),
    before,
  );
  await page.getByRole("status").filter({ hasText: "Component uninstalled." }).waitFor();

  await page.getByRole("button", { name: "Previous page", exact: true }).click();
  const applicationCard = cardByHeading(page, "Code Server");
  await applicationCard.getByRole("button", { name: "Inspect", exact: true }).click();
  const applicationLink = page.getByRole("link", { name: "Open Application management", exact: true });
  if ((await applicationLink.getAttribute("href")) !== "/applications") {
    throw new Error("Application Marketplace detail does not link to canonical Application management");
  }
  const applicationDetailText = await page.locator("body").innerText();
  requireText(applicationDetailText, "Application ownership", "Application owner-domain boundary");
  requireText(applicationDetailText, "Definition / manifest", "Application Marketplace manifest");
  requireText(applicationDetailText, "Update unsupported", "Application Marketplace owner capability");
  if (await page.getByRole("button", { name: "Preview update", exact: true }).count()) {
    throw new Error("Application Marketplace exposed an unsupported artifact-version update");
  }
  for (const forbidden of ["Start application", "Stop application", "Restart application"]) {
    if (applicationDetailText.includes(forbidden)) {
      throw new Error(`Marketplace leaked Application runtime lifecycle action: ${forbidden}`);
    }
  }

  await page.getByRole("button", { name: "All", exact: true }).click();
  await searchInput.fill("does-not-exist-marketplace-item");
  await page.getByText("No Marketplace items match", { exact: true }).waitFor();
  await searchInput.fill("");

  await page.goto(`${baseUrl}/tests/marketplaceHarness.html?provider=disabled`);
  await page.getByRole("status").getByText("Marketplace provider disabled", { exact: true }).waitFor();

  await page.goto(`${baseUrl}/tests/marketplaceHarness.html?provider=unavailable`);
  await page.getByRole("status").getByText("Marketplace provider unavailable", { exact: true }).waitFor();

  await page.goto(`${baseUrl}/tests/marketplaceHarness.html?mutation=fail`);
  await page.getByRole("heading", { name: "ProjectAtlas", exact: true }).waitFor();
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  const failingSkillCard = cardByHeading(page, "Code Review Skill");
  await failingSkillCard.getByRole("button", { name: "Inspect", exact: true }).click();
  await page.getByRole("button", { name: "Preview install", exact: true }).waitFor();
  await page.getByRole("button", { name: "Preview install", exact: true }).click();
  await page.getByRole("button", { name: "Install component", exact: true }).waitFor();
  await page.getByRole("button", { name: "Install component", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Marketplace owner mutation failed" }).waitFor();

  await page.goto(`${baseUrl}/tests/memoryTypesHarness.html`);
  await page.getByRole("heading", { name: "Memory", exact: true }).waitFor();

  const queryCard = cardByHeading(page, "Scope and query");
  const createCard = cardByHeading(page, "Create Memory explicitly");
  const entriesCard = cardByHeading(page, "Memory entries");
  const queryScope = queryCard.locator("select").nth(0);
  const queryMemoryType = queryCard.locator("select").nth(1);
  const queryScopeId = queryCard.locator('input:not([type="checkbox"])').nth(0);
  const createScope = createCard.locator("select").nth(0);
  const createMemoryType = createCard.locator("select").nth(2);
  const createScopeId = createCard.locator("input").nth(0);
  const createValue = createCard.locator("textarea").nth(0);

  await queryScope.selectOption("short_term");
  await queryScopeId.fill("session-browser");

  await createScope.selectOption("short_term");
  await createScopeId.fill("session-browser");
  await createMemoryType.selectOption("procedural");
  await createValue.fill('{"workflow":"compile-release"}');
  await createCard.getByRole("button", { name: "Create Memory", exact: true }).click();
  await createCard.getByRole("status").filter({ hasText: "type procedural" }).waitFor();

  const inventoryAfterCreate = await entriesCard.innerText();
  requireText(inventoryAfterCreate, "Type", "Memory inventory type column");
  requireText(inventoryAfterCreate, "procedural", "Memory inventory type value");

  const beforeTypeFilter = await page.evaluate(() => window.__memoryTypeCalls.length);
  await queryMemoryType.selectOption("procedural");
  await page.waitForFunction(
    (count) =>
      window.__memoryTypeCalls.slice(count).some((call) =>
        decodeURIComponent(call.url).includes("filter[memory_type]=procedural"),
      ),
    beforeTypeFilter,
  );

  const filteredInventory = await entriesCard.innerText();
  requireText(filteredInventory, "procedural", "Filtered Memory inventory");

  const beforeAllTypes = await page.evaluate(() => window.__memoryTypeCalls.length);
  await queryMemoryType.selectOption("all");
  await page.waitForFunction(
    (count) => window.__memoryTypeCalls.length > count,
    beforeAllTypes,
  );
  const allTypeCalls = await page.evaluate((count) => window.__memoryTypeCalls.slice(count), beforeAllTypes);
  const allTypesListCall = [...allTypeCalls].reverse().find(
    (call) => call.method === "GET" && call.url.startsWith("/api/v1/memory?"),
  );
  if (!allTypesListCall || decodeURIComponent(allTypesListCall.url).includes("filter[memory_type]")) {
    throw new Error(`All-types Memory query leaked a type filter: ${JSON.stringify(allTypeCalls)}`);
  }

  await queryMemoryType.selectOption("procedural");
  await entriesCard.locator("tbody a").first().click();
  await page.getByRole("heading", { name: "Memory detail", exact: true }).waitFor();

  const detailCard = cardByHeading(page, "Scope, type, provenance and retention");
  const detailText = await detailCard.innerText();
  requireText(detailText, "Memory Type", "Memory detail type label");
  requireText(detailText, "procedural", "Memory detail type value");

  const updateCard = cardByHeading(page, "Supersede with an explicit update");
  const promoteCard = cardByHeading(page, "Promote short-term Memory");
  const updateText = await updateCard.innerText();
  const promoteText = await promoteCard.innerText();
  if (updateText.includes("Memory Type") && !updateText.includes("preserve")) {
    throw new Error("Ordinary Memory update unexpectedly exposes a Memory Type mutation control");
  }
  if (promoteText.includes("Memory Type") && !promoteText.includes("preserves")) {
    throw new Error("Memory promotion unexpectedly exposes a Memory Type mutation control");
  }

  const beforeUpdate = await page.evaluate(() => window.__memoryTypeCalls.length);
  await updateCard.locator("textarea").nth(0).fill('{"workflow":"compile-release-v2"}');
  await updateCard.getByRole("button", { name: "Create superseding Memory", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__memoryTypeCalls.slice(count).some(
        (call) => call.method === "POST" && call.url === "/api/v1/commands/memory.update",
      ),
    beforeUpdate,
  );
  await page.getByRole("status").filter({ hasText: "type procedural" }).waitFor();

  const updateCalls = await page.evaluate((count) => window.__memoryTypeCalls.slice(count), beforeUpdate);
  const updateCall = updateCalls.find((call) => call.url === "/api/v1/commands/memory.update");
  if (!updateCall || Object.prototype.hasOwnProperty.call(updateCall.body ?? {}, "memory_type")) {
    throw new Error(`Memory update mutated Memory Type: ${JSON.stringify(updateCalls)}`);
  }

  const beforePromote = await page.evaluate(() => window.__memoryTypeCalls.length);
  await promoteCard.locator("input").nth(0).fill("user-browser");
  await promoteCard.getByRole("button", { name: "Promote Memory", exact: true }).click();
  await page.waitForFunction(
    (count) =>
      window.__memoryTypeCalls.slice(count).some(
        (call) => call.method === "POST" && call.url === "/api/v1/commands/memory.promote",
      ),
    beforePromote,
  );
  await page.getByRole("status").filter({ hasText: "type procedural" }).waitFor();

  const promoteCalls = await page.evaluate((count) => window.__memoryTypeCalls.slice(count), beforePromote);
  const promoteCall = promoteCalls.find((call) => call.url === "/api/v1/commands/memory.promote");
  if (!promoteCall || Object.prototype.hasOwnProperty.call(promoteCall.body ?? {}, "memory_type")) {
    throw new Error(`Memory promotion mutated Memory Type: ${JSON.stringify(promoteCalls)}`);
  }

  const memoryTypes = await page.evaluate(() =>
    [...window.__memoryTypeState.memories.values()].map((entry) => entry.memory_type),
  );
  if (memoryTypes.length < 3 || memoryTypes.some((memoryType) => memoryType !== "procedural")) {
    throw new Error(`Memory lifecycle changed canonical Memory Type: ${JSON.stringify(memoryTypes)}`);
  }
} finally {
  if (browser) await browser.close();
  vite.kill("SIGTERM");
}
