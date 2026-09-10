import { type AnchorHTMLAttributes, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "../api/client";
import { ControlPlaneCollectionClient } from "../api/collections";
import { ConfigurationClient } from "../api/configuration";
import {
  AgentConfigurationPage,
  AgentTeamConfigurationPage,
  emptyAgentProfile,
  emptyTeamProfile,
  validateAgent,
  validateTeam,
} from "./AgentConfigurationPage";

vi.mock("../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

function clients() {
  const fetchImpl = vi.fn(async () => {
    throw new Error("SSR editor coverage must not perform network requests");
  }) as unknown as typeof fetch;
  return {
    core: new ControlPlaneClient({ fetchImpl }),
    configuration: new ConfigurationClient({ fetchImpl }),
    collections: new ControlPlaneCollectionClient({ fetchImpl }),
  };
}

describe("Agent single-node configuration", () => {
  it("renders the canonical create editor instead of a raw object form", () => {
    const { core, configuration, collections } = clients();
    const html = renderToStaticMarkup(
      <AgentConfigurationPage
        core={core}
        configuration={configuration}
        collections={collections}
        mode="create"
      />,
    );

    expect(html).toContain("Create Agent");
    expect(html).toContain("Identity &amp; scope");
    expect(html).toContain("Model policy");
    expect(html).toContain("Allowed capabilities");
    expect(html).toContain("Memory &amp; knowledge");
    expect(html).toContain("Authorization profile ref");
    expect(html).toContain("Create Agent");
  });

  it("validates identity, instructions and user-memory coupling before mutation", () => {
    const empty = emptyAgentProfile();
    expect(validateAgent(empty)).toBe("Agent name is required.");

    const missingRole = { ...empty, name: "Reviewer" };
    expect(validateAgent(missingRole)).toBe("Agent role is required.");

    const userMemory = {
      ...empty,
      name: "Reviewer",
      role: "reviewer",
      data_access: {
        ...empty.data_access,
        memory_scopes: ["user"],
        allow_user_memory: false,
      },
    };
    expect(validateAgent(userMemory)).toBe("User memory scope requires Allow user memory.");
  });
});

describe("Agent Team single-node configuration", () => {
  it("renders pinned-member and runtime-limit configuration", () => {
    const { core, configuration } = clients();
    const html = renderToStaticMarkup(
      <AgentTeamConfigurationPage core={core} configuration={configuration} mode="create" />,
    );

    expect(html).toContain("Create Agent Team");
    expect(html).toContain("Pinned Agent members");
    expect(html).toContain("Coordination &amp; runtime limits");
    expect(html).toContain("Max parallel Agents");
    expect(html).toContain("Shared capabilities");
  });

  it("rejects empty Teams, invalid leaders and delegation outside pinned members", () => {
    const empty = emptyTeamProfile();
    expect(validateTeam(empty)).toBe("Team name is required.");

    const memberA = {
      agent: { agent_id: "agent_a", revision: 2 },
      role: "lead",
      required: true,
      can_delegate_to: [] as string[],
    };
    const memberB = {
      agent: { agent_id: "agent_b", revision: 4 },
      role: "worker",
      required: true,
      can_delegate_to: [] as string[],
    };
    const valid = { ...empty, name: "Research Team", members: [memberA, memberB] };
    expect(validateTeam(valid)).toBeNull();
    expect(validateTeam({ ...valid, leader_agent_id: "agent_missing" })).toBe(
      "Team leader must be one of the pinned members.",
    );
    expect(validateTeam({
      ...valid,
      members: [{ ...memberA, can_delegate_to: ["agent_missing"] }, memberB],
    })).toBe("Delegation targets must be other pinned Team members.");
    expect(validateTeam({
      ...valid,
      members: [{ ...memberA, can_delegate_to: ["agent_a"] }, memberB],
    })).toBe("Delegation targets must be other pinned Team members.");
  });
});
