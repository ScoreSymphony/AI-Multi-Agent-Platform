const CONTEXT_KEY = "agent-platform:organization-context";

export interface CollaborationContext {
  organizationId: string | null;
  teamId: string | null;
}

const EMPTY_CONTEXT: CollaborationContext = { organizationId: null, teamId: null };

export function parseStoredContext(raw: string | null): CollaborationContext {
  if (!raw) return EMPTY_CONTEXT;
  try {
    const parsed = JSON.parse(raw) as Partial<CollaborationContext>;
    return {
      organizationId: typeof parsed.organizationId === "string" ? parsed.organizationId : null,
      teamId: typeof parsed.teamId === "string" ? parsed.teamId : null,
    };
  } catch {
    return EMPTY_CONTEXT;
  }
}

export function loadCollaborationContext(): CollaborationContext {
  try {
    return parseStoredContext(localStorage.getItem(CONTEXT_KEY));
  } catch {
    return EMPTY_CONTEXT;
  }
}

export function persistCollaborationContext(context: CollaborationContext): void {
  try {
    localStorage.setItem(CONTEXT_KEY, JSON.stringify(context));
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
}
