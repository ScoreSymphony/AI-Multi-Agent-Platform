export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
  limit: number;
}

export interface AuthenticatedActor {
  actor_id: string;
  actor_type: string;
  authentication_method: string;
  credential_id: string | null;
  authenticated_at: string;
  expires_at: string | null;
  organization_id: string | null;
  project_id: string | null;
}

export interface HealthStatus {
  status: string;
  [key: string]: unknown;
}

export interface CanonicalTask {
  id: string;
  type?: string;
  title: string;
  objective: string;
  status: string;
  project_id?: string | null;
  workspace_id?: string | null;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface CanonicalRun {
  id: string;
  type?: string;
  task_id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  [key: string]: unknown;
}

export interface CanonicalReference {
  id: string;
  type: string;
  task_id: string;
  [key: string]: unknown;
}

export interface CanonicalApproval {
  id: string;
  type: "approval";
  status: string;
  action: string;
  resource_type: string;
  resource_id: string;
  requested_action_digest: string;
  risk: string;
  reason: string;
  created_at: string;
  expires_at: string;
  [key: string]: unknown;
}

export interface CanonicalVerification {
  id: string;
  type: "verification";
  task_id: string;
  run_id: string | null;
  status: string;
  requested_verifier_kind: string;
  created_at: string;
  [key: string]: unknown;
}

export interface CanonicalNotification {
  id: string;
  type: "notification";
  category: string;
  severity: string;
  title: string;
  state: string;
  task_id: string | null;
  run_id: string | null;
  approval_id: string | null;
  verification_id: string | null;
  created_at: string;
  [key: string]: unknown;
}

export interface CanonicalAgent {
  id: string;
  type: string;
  current_revision?: number;
  updated_at?: string;
  [key: string]: unknown;
}

export interface CanonicalWorker {
  id: string;
  type: string;
  status?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface SearchResult {
  id: string;
  type: string;
  title?: string | null;
  status?: string | null;
  [key: string]: unknown;
}

export interface SearchPage {
  items: SearchResult[];
  next_cursor: string | null;
  total: number;
  limit: number;
}
