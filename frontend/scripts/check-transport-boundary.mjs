import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const apiDirectory = fileURLToPath(new URL("../src/api/", import.meta.url));

// Temporary migration inventory. Remove a module from this set as soon as it
// delegates generic HTTP concerns to ApiTransport. New modules must never be
// added merely to bypass the boundary.
const legacyDomainTransportModules = new Set([
  "automations.ts",
  "configuration.ts",
  "conversationResponses.ts",
  "conversations.ts",
  "evaluations.ts",
  "governance.ts",
  "integrations.ts",
  "learning.ts",
  "memoryKnowledge.ts",
  "organizations.ts",
  "repositories.ts",
  "templates.ts",
]);

const approvedLowLevelModules = new Set(["browserSession.ts", "transport.ts"]);
const violations = [];

for (const file of readdirSync(apiDirectory)) {
  if (!file.endsWith(".ts") || file.endsWith(".test.ts")) continue;
  const content = readFileSync(new URL(`../src/api/${file}`, import.meta.url), "utf8");
  const ownsJsonParsing = /function\s+safeJson\s*\(/.test(content);
  const ownsErrorNormalization = /function\s+normalizeError\s*\(/.test(content);
  const invokesInjectedFetch = /\bthis\.fetchImpl\s*\(/.test(content);
  const constructsControlPlaneRequest =
    content.includes('"X-Correlation-ID"') && content.includes('credentials: "include"');
  const ownsGenericTransport =
    ownsJsonParsing || ownsErrorNormalization || invokesInjectedFetch || constructsControlPlaneRequest;

  if (!ownsGenericTransport) continue;
  if (approvedLowLevelModules.has(file) || legacyDomainTransportModules.has(file)) continue;
  violations.push(file);
}

if (violations.length > 0) {
  console.error(
    [
      "Generic frontend HTTP transport logic must live in src/api/transport.ts",
      "or the approved browser-session boundary. Domain clients must delegate to ApiTransport.",
      `Unexpected transport owners: ${violations.sort().join(", ")}`,
    ].join("\n"),
  );
  process.exitCode = 1;
}
