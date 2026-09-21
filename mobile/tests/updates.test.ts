import { describe, expect, it, vi } from "vitest";

import {
  discoverOfficialMobileUpdate,
  requireOfficialUpdateUrl,
} from "../src/updates";

function officialRelease(version: string, assetUrl?: string) {
  const tag = `mobile-v${version}`;
  return {
    tag_name: tag,
    html_url: `https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/releases/tag/${tag}`,
    draft: false,
    prerelease: false,
    body: `Release notes for ${version}`,
    assets: [
      {
        name: `AI-Multi-Agent-Mobile-v${version}.apk`,
        browser_download_url:
          assetUrl ??
          `https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/releases/download/${tag}/AI-Multi-Agent-Mobile-v${version}.apk`,
      },
      {
        name: "mobile-release.json",
        browser_download_url:
          `https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/releases/download/${tag}/mobile-release.json`,
      },
    ],
  };
}

function manifest(version: string) {
  return {
    schema_version: 1,
    release_kind: "android-apk",
    tag: `mobile-v${version}`,
    version,
    android_package: "org.scoresymphony.aimultiagentplatform",
    apk_asset: `AI-Multi-Agent-Mobile-v${version}.apk`,
    apk_sha256: "a".repeat(64),
  };
}

describe("official mobile update discovery", () => {
  it("reports a newer official GitHub APK without installing it", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify([officialRelease("0.2.0")]), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(manifest("0.2.0")), { status: 200 }),
      );

    const update = await discoverOfficialMobileUpdate("0.1.0", fetchImpl);

    expect(update.state).toBe("update_available");
    expect(update.latestVersion).toBe("0.2.0");
    expect(update.apkUrl).toBe(
      "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/releases/download/mobile-v0.2.0/AI-Multi-Agent-Mobile-v0.2.0.apk",
    );
    expect(update.apkSha256).toBe("a".repeat(64));
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("reports current and no-metadata states explicitly", async () => {
    const currentFetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify([officialRelease("0.1.0")]), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(manifest("0.1.0")), { status: 200 }),
      );
    await expect(discoverOfficialMobileUpdate("0.1.0", currentFetch)).resolves.toMatchObject({
      state: "current",
      apkUrl: null,
    });

    const emptyFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    );
    await expect(discoverOfficialMobileUpdate("0.1.0", emptyFetch)).resolves.toMatchObject({
      state: "no_metadata",
    });
  });

  it("rejects malicious or server-provided update URLs", async () => {
    expect(() => requireOfficialUpdateUrl("https://evil.example/update.apk")).toThrow(
      "untrusted",
    );

    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify([
          officialRelease("0.2.0", "https://evil.example/AI-Multi-Agent-Mobile-v0.2.0.apk"),
        ]),
        { status: 200 },
      ),
    );
    const result = await discoverOfficialMobileUpdate("0.1.0", fetchImpl);
    expect(result.state).toBe("check_failed");
    expect(result.apkUrl).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
