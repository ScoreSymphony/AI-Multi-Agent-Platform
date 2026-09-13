import { describe, expect, it } from "vitest";
import { parseStoredContext } from "./collaborationContext";
import { splitCsv } from "./organizationFormInput";

describe("Organization presentation state", () => {
  it("restores only valid string collaboration identifiers", () => {
    expect(parseStoredContext('{"organizationId":"org_1","teamId":"team_2"}')).toEqual({
      organizationId: "org_1",
      teamId: "team_2",
    });
    expect(parseStoredContext('{"organizationId":42,"teamId":false}')).toEqual({
      organizationId: null,
      teamId: null,
    });
    expect(parseStoredContext("not-json")).toEqual({ organizationId: null, teamId: null });
  });

  it("normalizes editable role and policy references without duplicating entries", () => {
    expect(splitCsv("role:member, role:admin, role:member,  ")).toEqual(["role:member", "role:admin"]);
  });
});
