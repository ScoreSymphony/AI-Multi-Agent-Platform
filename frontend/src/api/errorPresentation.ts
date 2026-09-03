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

  if (error.body.code === "unavailable") {
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
