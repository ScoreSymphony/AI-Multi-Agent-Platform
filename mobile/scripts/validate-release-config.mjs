import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const EXPECTED_ANDROID_PACKAGE = "org.scoresymphony.aimultiagentplatform";
const SEMVER = /^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$/;

function readJson(name) {
  return JSON.parse(readFileSync(resolve(ROOT, name), "utf8"));
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

const packageDocument = readJson("package.json");
const appDocument = readJson("app.json");
const expo = appDocument.expo ?? {};
const android = expo.android ?? {};

if (!SEMVER.test(packageDocument.version ?? "")) {
  fail("mobile/package.json must contain a semantic version");
}
if (expo.version !== packageDocument.version) {
  fail("mobile package.json version and Expo version must match");
}
if (android.package !== EXPECTED_ANDROID_PACKAGE) {
  fail(
    `Android package must remain ${EXPECTED_ANDROID_PACKAGE}; got ${String(android.package)}`,
  );
}
if (!Number.isInteger(android.versionCode) || android.versionCode < 1) {
  fail("Expo android.versionCode must be a positive integer");
}

const expectedVersion = process.env.AI_MAP_MOBILE_EXPECT_VERSION?.trim();
if (expectedVersion && expectedVersion !== expo.version) {
  fail(
    `Requested mobile release version ${expectedVersion} does not match configured version ${expo.version}`,
  );
}

const expectedVersionCode = process.env.AI_MAP_MOBILE_EXPECT_VERSION_CODE?.trim();
if (
  expectedVersionCode &&
  Number.parseInt(expectedVersionCode, 10) !== android.versionCode
) {
  fail(
    `Requested Android versionCode ${expectedVersionCode} does not match configured versionCode ${android.versionCode}`,
  );
}

process.stdout.write(
  JSON.stringify(
    {
      android_package: android.package,
      android_version_code: android.versionCode,
      version: expo.version,
    },
    null,
    2,
  ),
);
