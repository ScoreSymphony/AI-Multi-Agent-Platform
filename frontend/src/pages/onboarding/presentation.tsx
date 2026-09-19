import type { CanonicalModel, JsonValue } from "../../api/types";
import type { FirstRunTaskResult, MultiAgentFirstRunResult, OnboardingStatus } from "../../api/onboarding";
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

export function MultiAgentFirstResult({ result }: { result: MultiAgentFirstRunResult }) {
  return (
    <Card title="Official multi-agent first-run result">
      <div className="metrics">
        <Metric label="Task" value={result.task_status} />
        <Metric label="Plan steps" value={result.steps.length} />
        <Metric label="Specialized roles" value={Object.keys(result.agents).length} />
        <Metric label="Verification" value={result.review.verification_status} />
        <Metric label="Artifacts" value={result.artifact_ids.length} />
      </div>
      <div className="actions">
        <AppLink href={`/tasks/${result.task_id}`}>Open Task</AppLink>
        {result.result_id ? <AppLink href={`/results/${result.result_id}`}>Open produced Result</AppLink> : null}
      </div>
      <div className="context-summary">
        <span>Participating roles</span>
        <strong>{Object.keys(result.agents).sort().join(" · ")}</strong>
      </div>
      <p>Plan <CanonicalId value={result.plan_id} /> uses the canonical #889 dependency graph. Root research and approach steps can run independently before execution fans in and the reviewer checks the exact output.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Step</th><th>Agent</th><th>Dependencies</th><th>Status</th><th>Run / Result</th></tr></thead>
          <tbody>{result.steps.map((step) => (
            <tr key={step.step_id}>
              <td>{step.title}<div><CanonicalId value={step.step_id} /></div></td>
              <td>{step.agent_id ? <CanonicalId value={`${step.agent_id}@${step.agent_revision ?? "?"}`} /> : "—"}</td>
              <td>{step.depends_on.length ? step.depends_on.map((id) => <div key={id}><CanonicalId value={id} /></div>) : "parallel root"}</td>
              <td><StatusBadge value={step.status} /><div><small>{step.phase}</small></div></td>
              <td>{step.run_id ? <AppLink href={`/runs/${step.run_id}`}>Run</AppLink> : "—"}{step.result_ids.map((id) => <div key={id}><AppLink href={`/results/${id}`}>Result</AppLink></div>)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="stack">
        <strong>Artifacts</strong>
        <ul>{result.artifact_ids.map((artifactId) => <li key={artifactId}><AppLink href={`/artifacts/${artifactId}`}><CanonicalId value={artifactId} /></AppLink></li>)}</ul>
      </div>
      <div className="stack">
        <strong>Canonical verification</strong>
        {result.verification.length ? <ul>{result.verification.map((item) => <li key={item.verification_id}><CanonicalId value={item.verification_id} /> · {item.subject_type}:{item.subject_id} · <StatusBadge value={item.outcome ?? item.status} />{item.is_final_result_review ? " · produced result" : ""}</li>)}</ul> : <p>No Verification record was produced.</p>}
      </div>
      <pre>{JSON.stringify(result.trace, null, 2)}</pre>
    </Card>
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
