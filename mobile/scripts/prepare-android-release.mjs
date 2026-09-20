import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const target = resolve(process.argv[2] ?? resolve(ROOT, "android/app/build.gradle"));
const lines = readFileSync(target, "utf8").split("\n");

let inRelease = false;
let releaseIndent = -1;
let replacements = 0;

for (let index = 0; index < lines.length; index += 1) {
  const line = lines[index];
  const trimmed = line.trim();
  const indent = line.length - line.trimStart().length;

  if (!inRelease && trimmed === "release {") {
    inRelease = true;
    releaseIndent = indent;
    continue;
  }

  if (inRelease && trimmed === "}" && indent === releaseIndent) {
    inRelease = false;
    releaseIndent = -1;
    continue;
  }

  if (inRelease && trimmed === "signingConfig signingConfigs.debug") {
    lines[index] =
      " ".repeat(indent) +
      "// Production signing is applied after assembly by the release workflow.";
    replacements += 1;
  }
}

if (replacements !== 1) {
  throw new Error(
    `Expected exactly one debug signingConfig in the Android release block, found ${replacements}`,
  );
}

writeFileSync(target, lines.join("\n"), "utf8");
