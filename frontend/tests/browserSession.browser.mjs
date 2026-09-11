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
  await page.getByRole("heading", { name: "ProjectAtlas" }).waitFor();

  const marketplaceText = await page.locator("body").innerText();
  requireText(marketplaceText, "candidate", "Marketplace lifecycle presentation");
  requireText(marketplaceText, "evaluation:required", "Marketplace evaluation presentation");
  requireText(marketplaceText, "local", "Marketplace deployment presentation");
  requireText(marketplaceText, "compatible", "Marketplace cost presentation");

  const initialCalls = await page.evaluate(() => [...window.__marketplaceCalls]);
  const initialTechnicalCall = initialCalls.find((call) =>
    decodeURIComponent(call).includes("filter[technical_component]=true"),
  );
  if (!initialTechnicalCall) {
    throw new Error(`Marketplace did not default to technical components: ${initialCalls.join("\n")}`);
  }

  const previousCallCount = initialCalls.length;
  await page.getByRole("button", { name: "Code intelligence" }).click();
  await page.waitForFunction(
    (count) => window.__marketplaceCalls.length > count,
    previousCallCount,
  );

  const callsAfterCategoryClick = await page.evaluate(() => [...window.__marketplaceCalls]);
  const categoryCall = callsAfterCategoryClick.slice(previousCallCount).find((call) => {
    const decoded = decodeURIComponent(call);
    return (
      decoded.includes("filter[technical_component]=true") &&
      decoded.includes("filter[category]=code-intelligence")
    );
  });
  if (!categoryCall) {
    throw new Error(
      `Marketplace category navigation did not produce the expected Registry query: ${callsAfterCategoryClick.join("\n")}`,
    );
  }

  await page.goto(`${baseUrl}/tests/memoryTypesHarness.html`);
  await page.getByRole("heading", { name: "Memory", exact: true }).waitFor();

  const queryCard = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Scope and query", exact: true }),
  });
  const createCard = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Create Memory explicitly", exact: true }),
  });
  const entriesCard = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Memory entries", exact: true }),
  });

  await queryCard.getByLabel("Scope", { exact: true }).selectOption("short_term");
  await queryCard.getByLabel("Scope ID", { exact: true }).fill("session-browser");

  await createCard.getByLabel("Scope", { exact: true }).selectOption("short_term");
  await createCard.getByLabel("Scope ID", { exact: true }).fill("session-browser");
  await createCard.getByLabel("Memory Type", { exact: true }).selectOption("procedural");
  await createCard.getByLabel("Value JSON", { exact: true }).fill('{"workflow":"compile-release"}');
  await createCard.getByRole("button", { name: "Create Memory", exact: true }).click();
  await createCard.getByRole("status").filter({ hasText: "type procedural" }).waitFor();

  const inventoryAfterCreate = await entriesCard.innerText();
  requireText(inventoryAfterCreate, "Type", "Memory inventory type column");
  requireText(inventoryAfterCreate, "procedural", "Memory inventory type value");

  const beforeTypeFilter = await page.evaluate(() => window.__memoryTypeCalls.length);
  await queryCard.getByLabel("Memory Type", { exact: true }).selectOption("procedural");
  await page.waitForFunction(
    (count) => window.__memoryTypeCalls.slice(count).some((call) =>
      decodeURIComponent(call.url).includes("filter[memory_type]=procedural"),
    ),
    beforeTypeFilter,
  );

  const filteredInventory = await entriesCard.innerText();
  requireText(filteredInventory, "procedural", "Filtered Memory inventory");

  const beforeAllTypes = await page.evaluate(() => window.__memoryTypeCalls.length);
  await queryCard.getByLabel("Memory Type", { exact: true }).selectOption("all");
  await page.waitForFunction(
    (count) => window.__memoryTypeCalls.length > count,
    beforeAllTypes,
  );
  const allTypeCalls = await page.evaluate((count) => window.__memoryTypeCalls.slice(count), beforeAllTypes);
  const allTypesListCall = allTypeCalls.find((call) => call.method === "GET" && call.url.startsWith("/api/v1/memory?"));
  if (!allTypesListCall || decodeURIComponent(allTypesListCall.url).includes("filter[memory_type]")) {
    throw new Error(`All-types Memory query leaked a type filter: ${JSON.stringify(allTypeCalls)}`);
  }

  await queryCard.getByLabel("Memory Type", { exact: true }).selectOption("procedural");
  await entriesCard.locator("tbody a").first().click();
  await page.getByRole("heading", { name: "Memory detail", exact: true }).waitFor();

  const detailCard = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Scope, type, provenance and retention", exact: true }),
  });
  const detailText = await detailCard.innerText();
  requireText(detailText, "Memory Type", "Memory detail type label");
  requireText(detailText, "procedural", "Memory detail type value");

  const updateCard = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Supersede with an explicit update", exact: true }),
  });
  const promoteCard = page.locator("section.card").filter({
    has: page.getByRole("heading", { name: "Promote short-term Memory", exact: true }),
  });
  if (await updateCard.getByLabel("Memory Type", { exact: true }).count()) {
    throw new Error("Ordinary Memory update unexpectedly exposes a Memory Type mutation control");
  }
  if (await promoteCard.getByLabel("Memory Type", { exact: true }).count()) {
    throw new Error("Memory promotion unexpectedly exposes a Memory Type mutation control");
  }

  const beforeUpdate = await page.evaluate(() => window.__memoryTypeCalls.length);
  await updateCard.getByLabel("Replacement value JSON", { exact: true }).fill('{"workflow":"compile-release-v2"}');
  await updateCard.getByRole("button", { name: "Create superseding Memory", exact: true }).click();
  await page.waitForFunction(
    (count) => window.__memoryTypeCalls.slice(count).some((call) =>
      call.method === "POST" && call.url === "/api/v1/commands/memory.update"),
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
  await promoteCard.getByLabel("Target scope ID", { exact: true }).fill("user-browser");
  await promoteCard.getByRole("button", { name: "Promote Memory", exact: true }).click();
  await page.waitForFunction(
    (count) => window.__memoryTypeCalls.slice(count).some((call) =>
      call.method === "POST" && call.url === "/api/v1/commands/memory.promote"),
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
