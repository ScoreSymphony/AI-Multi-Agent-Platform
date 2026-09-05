import { useEffect, useState } from "react";
import { OnboardingClient, type OnboardingStatus } from "../api/onboarding";
import { AppLink } from "../app/router";

export function OnboardingCallout({ client }: { client: OnboardingClient }) {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);

  useEffect(() => {
    let active = true;
    void client.status().then(
      (nextStatus) => {
        if (active) setStatus(nextStatus);
      },
      () => {
        if (active) setStatus(null);
      },
    );
    return () => {
      active = false;
    };
  }, [client]);

  if (status === null || status.state === "ready_for_task") return null;

  return (
    <aside className="state state-warning" aria-label="First-run onboarding">
      <strong>First-run setup is incomplete.</strong>
      <p>
        Current state: <code>{status.state}</code>. Continue the guided local/self-hosted setup
        before starting the first Assistant task.
      </p>
      <AppLink href="/onboarding">Continue first-run onboarding</AppLink>
    </aside>
  );
}
