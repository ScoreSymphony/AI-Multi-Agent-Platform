export interface ApprovalOwnerRef {
  type: string;
  id: string;
}

export interface CanonicalApproval {
  id: string;
  type: "approval";
  status: string;
  subject_type: string;
  subject_id: string;
  owner_ref: ApprovalOwnerRef;
  requester_ref: string;
  action: string;
  resource_type: string;
  resource_id: string;
  requested_action_digest: string;
  risk: string;
  policy_id: string;
  reason: string;
  project_id: string | null;
  task_id: string | null;
  run_id: string | null;
  capability_ref: string | null;
  payload_ref: string | null;
  created_at: string;
  expires_at: string;
  decision_by: ApprovalOwnerRef | null;
  decision_at: string | null;
  decision_comment: string | null;
}
