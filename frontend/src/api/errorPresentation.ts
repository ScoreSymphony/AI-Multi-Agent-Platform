import { isControlPlaneError } from "./client";

export interface ErrorPresentation {
  title: string;
  message: string;
  hint?: string;
  reference?: string;
}

export function describeError(error: unknown): ErrorPresentation {
  if (!isControlPlaneError(error)) {
    return {
      title: "Request failed",
      message: error instanceof Error ? error.message : "Unknown error",
    };
  }

  const details = error.body.details ?? {};
  const authorizationOutcome = stringDetail(details.authorization_outcome);
  const approvalId = stringDetail(details.approval_id);

  if (error.status === 401 || error.body.code === "unauthorized") {
    return {
      title: "Authentication required",
      message: error.body.message,
      hint: "The request is unauthenticated. Sign-in or session handling belongs to the canonical authentication boundary.",
    };
  }

  if (authorizationOutcome === "require_approval") {
    return {
      title: "Approval required",
      message: error.body.message,
      hint: "The Control Plane requires approval for this exact action. The frontend cannot bypass or manufacture that approval.",
      reference: approvalId,
    };
  }

  if (error.status === 403 || error.body.code === "forbidden") {
    return {
      title: "Access denied",
      message: error.body.message,
      hint: "The Control Plane denied this operation. Client-side visibility or disabled controls are not an authorization boundary.",
    };
  }

  if (error.status === 404 || error.body.code === "not_found") {
    return {
      title: "Not found",
      message: error.body.message,
      hint: "The canonical resource may have been removed or is no longer visible to the current actor.",
    };
  }

  if (
    error.status === 422
    || error.body.category === "validation"
    || [
      "invalid_request",
      "invalid_configuration",
      "invalid_argument",
      "validation_error",
    ].includes(error.body.code)
  ) {
    return {
      title: "Validation failed",
      message: error.body.message,
      hint: "Review the submitted values. Validation remains authoritative on the Control Plane.",
    };
  }

  if (error.status === 409 || error.body.code === "conflict") {
    return {
      title: "State changed",
      message: error.body.message,
      hint: "Canonical state changed since this view was loaded. Refresh the resource before retrying the action.",
    };
  }

  if (
    error.status === 0
    || error.body.category === "transport"
    || ["network_failure", "request_timeout"].includes(error.body.code)
  ) {
    return {
      title: "Control Plane unavailable",
      message: error.body.message,
      hint: error.body.retryable ? "The request can be retried without creating browser-owned lifecycle state." : undefined,
    };
  }

  if (["unavailable", "model_unavailable"].includes(error.body.code)) {
    return {
      title: "Subsystem unavailable",
      message: error.body.message,
      hint: error.body.retryable ? "The Control Plane reports this failure as retryable." : undefined,
    };
  }

  return {
    title: "Request failed",
    message: `${error.body.category}: ${error.body.message}`,
    hint: error.body.retryable ? "The Control Plane reports this failure as retryable." : undefined,
  };
}

function stringDetail(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function canRetryError(error: unknown): boolean {
  return isControlPlaneError(error) && error.body.retryable;
}
