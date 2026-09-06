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
} finally {
  if (browser) await browser.close();
  vite.kill("SIGTERM");
}
