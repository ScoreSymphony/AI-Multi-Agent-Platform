import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = resolve(import.meta.dirname, "..");

function releaseConfig(): {
  version: string;
  androidPackage: string;
  versionCode: number;
} {
  const packageDocument = JSON.parse(
    readFileSync(resolve(ROOT, "package.json"), "utf8"),
  ) as { version: string };
  const appDocument = JSON.parse(
    readFileSync(resolve(ROOT, "app.json"), "utf8"),
  ) as {
    expo: {
      version: string;
      android: { package: string; versionCode: number };
    };
  };

  expect(appDocument.expo.version).toBe(packageDocument.version);
  return {
    version: appDocument.expo.version,
    androidPackage: appDocument.expo.android.package,
    versionCode: appDocument.expo.android.versionCode,
  };
}

function validateVersionFixture(version: string): string {
  const directory = mkdtempSync(resolve(tmpdir(), "mobile-semver-"));
  try {
    const packageDocument = JSON.parse(
      readFileSync(resolve(ROOT, "package.json"), "utf8"),
    ) as Record<string, unknown>;
    const appDocument = JSON.parse(
      readFileSync(resolve(ROOT, "app.json"), "utf8"),
    ) as { expo: { version: string } };

    packageDocument.version = version;
    appDocument.expo.version = version;

    mkdirSync(resolve(directory, "scripts"));
    writeFileSync(
      resolve(directory, "package.json"),
      JSON.stringify(packageDocument),
      "utf8",
    );
    writeFileSync(
      resolve(directory, "app.json"),
      JSON.stringify(appDocument),
      "utf8",
    );
    writeFileSync(
      resolve(directory, "scripts/validate-release-config.mjs"),
      readFileSync(resolve(ROOT, "scripts/validate-release-config.mjs"), "utf8"),
      "utf8",
    );

    return execFileSync(
      process.execPath,
      [resolve(directory, "scripts/validate-release-config.mjs")],
      {
        cwd: directory,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
        env: process.env,
      },
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

describe("Android release configuration", () => {
  it("keeps semantic version, package identifier and versionCode authoritative", () => {
    const packageDocument = JSON.parse(
      readFileSync(resolve(ROOT, "package.json"), "utf8"),
    ) as { scripts: Record<string, string> };
    const config = releaseConfig();

    expect(config.androidPackage).toBe("org.scoresymphony.aimultiagentplatform");
    expect(Number.isInteger(config.versionCode)).toBe(true);
    expect(config.versionCode).toBeGreaterThan(0);
    expect(packageDocument.scripts["validate:release"]).toContain(
      "validate-release-config.mjs",
    );
    expect(packageDocument.scripts["build:android:release"]).toContain(
      "assembleRelease",
    );
  });

  it("validates the maintained release config without mutating it", () => {
    const config = releaseConfig();
    const output = execFileSync(
      process.execPath,
      [resolve(ROOT, "scripts/validate-release-config.mjs")],
      {
        cwd: ROOT,
        encoding: "utf8",
        env: {
          ...process.env,
          AI_MAP_MOBILE_EXPECT_VERSION: config.version,
          AI_MAP_MOBILE_EXPECT_VERSION_CODE: String(config.versionCode),
        },
      },
    );
    expect(JSON.parse(output)).toEqual({
      android_package: config.androidPackage,
      android_version_code: config.versionCode,
      version: config.version,
    });
  });

  it("accepts valid prerelease and build identifiers", () => {
    for (const version of [
      "1.2.3-alpha.1",
      "1.2.3-01a+001",
      "1.2.3-alpha-beta+build.01",
    ]) {
      expect(() => validateVersionFixture(version)).not.toThrow();
    }
  });

  it("rejects malformed semantic-version identifiers before release work", () => {
    for (const version of [
      "1.2.3-alpha..1",
      "1.2.3-01",
      "1.2.3-alpha.",
      "1.2.3+.",
      "1.2.3+build..1",
    ]) {
      expect(() => validateVersionFixture(version)).toThrow();
    }
  });

  it("fails closed when a requested release version disagrees with app config", () => {
    const config = releaseConfig();
    expect(() =>
      execFileSync(
        process.execPath,
        [resolve(ROOT, "scripts/validate-release-config.mjs")],
        {
          cwd: ROOT,
          stdio: "pipe",
          env: {
            ...process.env,
            AI_MAP_MOBILE_EXPECT_VERSION: `${config.version}-mismatch`,
          },
        },
      ),
    ).toThrow();
  });

  it("removes debug signing only from the generated release build type", () => {
    const directory = mkdtempSync(resolve(tmpdir(), "mobile-gradle-"));
    const fixture = resolve(directory, "build.gradle");
    try {
      writeFileSync(
        fixture,
        [
          "android {",
          "    signingConfigs {",
          "        debug {",
          "            storeFile file('debug.keystore')",
          "        }",
          "    }",
          "    buildTypes {",
          "        debug {",
          "            signingConfig signingConfigs.debug",
          "        }",
          "        release {",
          "            signingConfig signingConfigs.debug",
          "            minifyEnabled false",
          "        }",
          "    }",
          "}",
          "",
        ].join("\n"),
        "utf8",
      );

      execFileSync(
        process.execPath,
        [resolve(ROOT, "scripts/prepare-android-release.mjs"), fixture],
        { cwd: ROOT },
      );

      const updated = readFileSync(fixture, "utf8");
      expect(updated).toContain(
        "debug {\n            signingConfig signingConfigs.debug",
      );
      expect(updated).toContain(
        "release {\n            // Production signing is applied after assembly by the release workflow.",
      );
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
