import type { CanonicalModel, JsonValue } from "../../api/types";
import type { FirstRunTaskResult, OnboardingStatus } from "../../api/onboarding";
import { AppLink } from "../../app/router";
import { CanonicalId, Card, StatusBadge } from "../../components/States";
import { onboardingStatePresentation } from "./state";

export function OnboardingStateSummary({ status }: { status: OnboardingStatus }) {
  const stateCopy = onboardingStatePresentation(status.state);
  return <div><StatusBadge value={status.state} /><h3>{stateCopy.title}</h3><p>{stateCopy.detail}</p></div>;
}

export function Guidance({ status }: { status: OnboardingStatus }) {
  const guidance = status.guidance.filter((item): item is string => typeof item === "string");
  if (!guidance.length) return null;
  return <ul>{guidance.map((item) => <li key={item}>{item}</li>)}</ul>;
}

export function Blockers({ blockers }: { blockers: Array<Record<string, JsonValue>> }) {
  return (
    <div className="state state-warning">
      <strong>Existing General Assistant configuration is not executable yet.</strong>
      {blockers.map((blocker, index) => (
        <div key={`${String(blocker.agent_id ?? "agent")}-${index}`}>
          <p>{typeof blocker.message === "string" ? blocker.message : "Execution preflight failed."}</p>
          <small>{typeof blocker.code === "string" ? `Code ${blocker.code}` : "Canonical preflight blocker"}{typeof blocker.agent_id === "string" ? ` · Agent ${blocker.agent_id}` : ""}</small>
        </div>
      ))}
    </div>
  );
}

export function ModelHealthTable({ models }: { models: CanonicalModel[] }) {
  const local = models.filter((model) => model.location === "local" || model.location === "self_hosted");
  if (!local.length) return null;
  return (
    <div className="table-wrap"><table><thead><tr><th>Model</th><th>Location</th><th>Provider</th><th>Effective health</th></tr></thead><tbody>{local.map((model) => <tr key={model.id}><td>{model.display_name}<div><CanonicalId value={model.id} /></div></td><td><StatusBadge value={model.location} /></td><td><CanonicalId value={model.provider_id} /></td><td><StatusBadge value={model.effective_health} /></td></tr>)}</tbody></table></div>
  );
}

export function FirstResult({ result }: { result: FirstRunTaskResult }) {
  return (
    <Card title="First canonical result">
      <div className="metrics"><Metric label="Task" value={result.task_status} /><Metric label="Run" value={result.run_status} /><Metric label="Agent" value={result.agent_id} /><Metric label="Result" value={result.result_id} /></div>
      <div className="actions"><AppLink href={`/tasks/${result.task_id}`}>Open Task</AppLink><AppLink href={`/runs/${result.run_id}`}>Open Run</AppLink><AppLink href={`/results/${result.result_id}`}>Open Result</AppLink></div>
      <pre>{JSON.stringify(result.output, null, 2)}</pre>
    </Card>
  );
}

export function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

export function UnavailableAction({ text }: { text: string }) {
  return <div className="state state-warning"><strong>Unavailable</strong><p>{text}</p></div>;
}
