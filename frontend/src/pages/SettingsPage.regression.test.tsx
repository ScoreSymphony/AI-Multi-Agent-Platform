import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SettingsSessionSecurityCopy } from "./SettingsPage";

describe("Settings session security copy", () => {
  it("keeps authentication, authorization and approval semantics product-neutral", () => {
    const html = renderToStaticMarkup(<SettingsSessionSecurityCopy />);

    expect(html).toContain("Authentication establishes identity only.");
    expect(html).toContain(
      "Authorization and approval decisions remain enforced server-side by the platform.",
    );
    expect(html).not.toMatch(/#\d+\b/);
  });
});
