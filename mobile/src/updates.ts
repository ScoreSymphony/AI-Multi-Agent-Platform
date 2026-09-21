import { MOBILE_ANDROID_PACKAGE } from "./appIdentity";

const REPOSITORY = "ScoreSymphony/AI-Multi-Agent-Platform";
const RELEASE_API = `https://api.github.com/repos/${REPOSITORY}/releases?per_page=30`;
const RELEASE_PAGE_PREFIX = `https://github.com/${REPOSITORY}/releases/tag/`;
const RELEASE_DOWNLOAD_PREFIX = `https://github.com/${REPOSITORY}/releases/download/`;

export type MobileUpdateState =
  | "current"
  | "update_available"
  | "no_metadata"
  | "check_failed";

export interface MobileUpdateInfo {
  state: MobileUpdateState;
  installedVersion: string;
  latestVersion: string | null;
  releasePageUrl: string | null;
  apkUrl: string | null;
  apkSha256: string | null;
  releaseNotes: string | null;
  message: string;
}

interface GitHubAsset {
  name?: unknown;
  browser_download_url?: unknown;
}

interface GitHubRelease {
  tag_name?: unknown;
  html_url?: unknown;
  draft?: unknown;
  prerelease?: unknown;
  body?: unknown;
  assets?: unknown;
}

interface MobileReleaseManifest {
  schema_version?: unknown;
  release_kind?: unknown;
  tag?: unknown;
  version?: unknown;
  android_package?: unknown;
  apk_asset?: unknown;
  apk_sha256?: unknown;
}

export async function discoverOfficialMobileUpdate(
  installedVersion: string,
  fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
): Promise<MobileUpdateInfo> {
  try {
    const releasesResponse = await fetchImpl(RELEASE_API, {
      method: "GET",
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!releasesResponse.ok) {
      return failed(installedVersion, `GitHub release metadata returned HTTP ${releasesResponse.status}`);
    }
    const releases = (await releasesResponse.json()) as unknown;
    if (!Array.isArray(releases)) {
      return failed(installedVersion, "GitHub release metadata was not a list");
    }

    const candidates = releases
      .filter(isRecord)
      .filter((release) => release.draft !== true && release.prerelease !== true)
      .map((release) => release as GitHubRelease)
      .filter((release) => typeof release.tag_name === "string" && release.tag_name.startsWith("mobile-v"))
      .map((release) => ({ release, version: String(release.tag_name).slice("mobile-v".length) }))
      .filter(({ version }) => parseSemver(version) !== null)
      .sort((left, right) => compareSemver(right.version, left.version));

    const latest = candidates[0];
    if (!latest) {
      return {
        state: "no_metadata",
        installedVersion,
        latestVersion: null,
        releasePageUrl: null,
        apkUrl: null,
        apkSha256: null,
        releaseNotes: null,
        message: "No official mobile-v* GitHub Release metadata is available yet.",
      };
    }

    const tag = `mobile-v${latest.version}`;
    const releasePageUrl = requireOfficialReleasePage(latest.release.html_url, tag);
    const assets = Array.isArray(latest.release.assets)
      ? latest.release.assets.filter(isRecord).map((item) => item as GitHubAsset)
      : [];
    const apkName = `AI-Multi-Agent-Mobile-v${latest.version}.apk`;
    const apkAsset = findAsset(assets, apkName);
    const manifestAsset = findAsset(assets, "mobile-release.json");
    if (!apkAsset || !manifestAsset) {
      return {
        state: "no_metadata",
        installedVersion,
        latestVersion: latest.version,
        releasePageUrl,
        apkUrl: null,
        apkSha256: null,
        releaseNotes: typeof latest.release.body === "string" ? latest.release.body : null,
        message: "The latest official mobile release is missing its required APK or release manifest.",
      };
    }

    const apkUrl = requireOfficialDownloadUrl(apkAsset.browser_download_url, tag, apkName);
    const manifestUrl = requireOfficialDownloadUrl(
      manifestAsset.browser_download_url,
      tag,
      "mobile-release.json",
    );
    const manifestResponse = await fetchImpl(manifestUrl, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!manifestResponse.ok) {
      return {
        state: "no_metadata",
        installedVersion,
        latestVersion: latest.version,
        releasePageUrl,
        apkUrl: null,
        apkSha256: null,
        releaseNotes: typeof latest.release.body === "string" ? latest.release.body : null,
        message: "The official mobile release manifest could not be loaded.",
      };
    }
    const manifest = (await manifestResponse.json()) as MobileReleaseManifest;
    validateManifest(manifest, tag, latest.version, apkName);

    const updateAvailable = compareSemver(latest.version, installedVersion) > 0;
    return {
      state: updateAvailable ? "update_available" : "current",
      installedVersion,
      latestVersion: latest.version,
      releasePageUrl,
      apkUrl: updateAvailable ? apkUrl : null,
      apkSha256: String(manifest.apk_sha256),
      releaseNotes: typeof latest.release.body === "string" ? latest.release.body : null,
      message: updateAvailable
        ? `Mobile ${latest.version} is available from the official GitHub Release.`
        : `Mobile ${installedVersion} is current relative to official GitHub Releases.`,
    };
  } catch (error) {
    return failed(
      installedVersion,
      error instanceof Error ? error.message : "Update discovery failed",
    );
  }
}

export function requireOfficialUpdateUrl(value: string): string {
  if (!value.startsWith(RELEASE_PAGE_PREFIX) && !value.startsWith(RELEASE_DOWNLOAD_PREFIX)) {
    throw new Error("Rejected untrusted mobile update URL");
  }
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.hostname !== "github.com") {
    throw new Error("Rejected untrusted mobile update URL");
  }
  return value;
}

function requireOfficialReleasePage(value: unknown, tag: string): string {
  if (typeof value !== "string") throw new Error("Official release page URL is missing");
  const expected = `${RELEASE_PAGE_PREFIX}${encodeURIComponent(tag)}`;
  if (value !== expected && value !== `${RELEASE_PAGE_PREFIX}${tag}`) {
    throw new Error("Rejected untrusted mobile release page URL");
  }
  return requireOfficialUpdateUrl(value);
}

function requireOfficialDownloadUrl(value: unknown, tag: string, asset: string): string {
  if (typeof value !== "string") throw new Error("Official release asset URL is missing");
  const expected = `${RELEASE_DOWNLOAD_PREFIX}${tag}/${asset}`;
  if (value !== expected) throw new Error("Rejected untrusted mobile release asset URL");
  return requireOfficialUpdateUrl(value);
}

function validateManifest(
  manifest: MobileReleaseManifest,
  tag: string,
  version: string,
  apkName: string,
): void {
  if (
    manifest.schema_version !== 1 ||
    manifest.release_kind !== "android-apk" ||
    manifest.tag !== tag ||
    manifest.version !== version ||
    manifest.android_package !== MOBILE_ANDROID_PACKAGE ||
    manifest.apk_asset !== apkName ||
    typeof manifest.apk_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/i.test(manifest.apk_sha256)
  ) {
    throw new Error("Official mobile release manifest failed provenance validation");
  }
}

function findAsset(assets: GitHubAsset[], name: string): GitHubAsset | null {
  return assets.find((asset) => asset.name === name) ?? null;
}

function failed(installedVersion: string, message: string): MobileUpdateInfo {
  return {
    state: "check_failed",
    installedVersion,
    latestVersion: null,
    releasePageUrl: null,
    apkUrl: null,
    apkSha256: null,
    releaseNotes: null,
    message,
  };
}

function compareSemver(left: string, right: string): number {
  const a = parseSemver(left);
  const b = parseSemver(right);
  if (!a || !b) throw new Error("Cannot compare invalid semantic versions");
  for (let index = 0; index < 3; index += 1) {
    const delta = a.core[index]! - b.core[index]!;
    if (delta !== 0) return delta;
  }
  if (a.prerelease.length === 0 && b.prerelease.length > 0) return 1;
  if (b.prerelease.length === 0 && a.prerelease.length > 0) return -1;
  const length = Math.max(a.prerelease.length, b.prerelease.length);
  for (let index = 0; index < length; index += 1) {
    const leftPart = a.prerelease[index];
    const rightPart = b.prerelease[index];
    if (leftPart === undefined) return -1;
    if (rightPart === undefined) return 1;
    if (leftPart === rightPart) continue;
    const leftNumber = /^[0-9]+$/.test(leftPart) ? Number(leftPart) : null;
    const rightNumber = /^[0-9]+$/.test(rightPart) ? Number(rightPart) : null;
    if (leftNumber !== null && rightNumber !== null) return leftNumber - rightNumber;
    if (leftNumber !== null) return -1;
    if (rightNumber !== null) return 1;
    return leftPart.localeCompare(rightPart);
  }
  return 0;
}

function parseSemver(value: string): { core: [number, number, number]; prerelease: string[] } | null {
  const match = /^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?(?:[+][0-9A-Za-z.-]+)?$/.exec(value);
  if (!match) return null;
  return {
    core: [Number(match[1]), Number(match[2]), Number(match[3])],
    prerelease: match[4] ? match[4].split(".") : [],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
