import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  OnboardingClient,
  type ComponentSetupMode,
  type ComponentSetupStatus,
} from "../../api/onboarding";
import {
  SetupClient,
  type SetupProductCard,
  type SetupRegistrySelection,
  type SetupSessionStatus,
  type SetupStepId,
} from "../../api/setup";
import type { APImanifest } from "../../api/types";
import { Card, ErrorState, LoadingState, StatusBadge } from "../../components/States";

const STEP_LABELS: Record<SetupStepId, string> = {
  identity: "Administrator",
  environment: "Environment",
  components: "Applications & components",
  configuration: "Configuration & credentials",
  validation: "Validation",
  ready: "Ready",
};

const CATEGORY_LABELS: Record<string, string> = {
  orchestrator: "Orchestration",
  model_provider: "Models",
  executor: "Execution",
  memory_knowledge: "Memory & knowledge",
  tools_mcp: "Tools & integrations",
  storage: "Storage",
  compute: "Compute",
};

const MODE_LABELS: Record<ComponentSetupMode, string> = {
  auto: "Recommended / Auto",
  local: "Local only",
  multi_node: "Existing servers / Multi-node",
  advanced: "Advanced / Custom",
};

const MODE_DETAILS: Record<ComponentSetupMode, string> = {
  auto: "Select the compatible recommended defaults discovered for this environment.",
  local: "Prefer compatible components that run on this local node.",
  multi_node: "Prefer the existing multi-node or distributed runtime components when available.",
  advanced: "Choose explicit component defaults in the advanced component profile editor.",
};

interface SetupLifecyclePanelProps {
  setup: SetupClient;
  onboarding: OnboardingClient;
  manifest?: APImanifest | null;
  onStateChange?: (status: SetupSessionStatus) => void;
}

export function SetupLifecyclePanel({
  setup,
  onboarding,
  manifest,
  onStateChange,
}: SetupLifecyclePanelProps) {
  const manifestKnown = manifest !== undefined && manifest !== null;
  const available = manifestKnown ? manifest.resources.includes("setup-sessions") : true;
  const componentSetupAvailable = manifestKnown
    ? manifest.resources.includes("component-setup")
    : true;
  const updateAvailable = manifestKnown
    ? (manifest.commands?.includes("onboarding.update-setup-session") ?? false)
    : true;
  const profileSaveAvailable = manifestKnown
    ? (manifest.commands?.includes("onboarding.save-component-profile") ?? false)
    : true;
  const provisionAvailable = manifestKnown
    ? (manifest.commands?.includes("onboarding.provision-setup") ?? false)
    : true;
  const validateAvailable = manifestKnown
    ? (manifest.commands?.includes("onboarding.validate-setup") ?? false)
    : true;
  const [status, setStatus] = useState<SetupSessionStatus | null>(null);
  const [componentSetupStatus, setComponentSetupStatus] = useState<ComponentSetupStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const snapshotEpoch = useRef(0);
  const mutationSequence = useRef(0);

  const load = useCallback(async () => {
    if (!available) return;
    const startedAt = snapshotEpoch.current;
    setLoading(true);
    try {
      const [next, nextComponentSetup] = await Promise.all([
        setup.status(),
        componentSetupAvailable ? onboarding.componentSetup() : Promise.resolve(null),
      ]);
      if (startedAt !== snapshotEpoch.current) return;
      setStatus(next);
      setComponentSetupStatus(nextComponentSetup);
      setError(null);
      onStateChange?.(next);
    } catch (nextError) {
      if (startedAt !== snapshotEpoch.current) return;
      setError(nextError);
    } finally {
      if (startedAt === snapshotEpoch.current) setLoading(false);
    }
  }, [available, componentSetupAvailable, onStateChange, onboarding, setup]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedRegistryRefs = useMemo(
    () => new Set(status?.registry_items.map((item) => registryRef(item)) ?? []),
    [status],
  );

  if (!available) {
    return (
      <Card title="Guided initial setup">
        <p>
          This Control Plane does not advertise the persistent setup lifecycle. The canonical
          onboarding and component-profile APIs remain available independently.
        </p>
      </Card>
    );
  }
  if (loading && status === null) return <LoadingState label="Loading persistent setup state…" />;
  if (error && status === null) return <ErrorState error={error} onRetry={() => void load()} />;
  if (status === null) return <LoadingState label="Loading persistent setup state…" />;
  const loadedStatus = status;

  async function mutate(label: string, operation: () => Promise<SetupSessionStatus>, success: string) {
    const mutationId = mutationSequence.current + 1;
    mutationSequence.current = mutationId;
    snapshotEpoch.current += 1;
    setLoading(false);
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const next = await operation();
      if (mutationId !== mutationSequence.current) return;
      snapshotEpoch.current += 1;
      setLoading(false);
      setStatus(next);
      onStateChange?.(next);
      setNotice(success);
    } catch (nextError) {
      if (mutationId !== mutationSequence.current) return;
      snapshotEpoch.current += 1;
      setLoading(false);
      setError(nextError);
    } finally {
      if (mutationId === mutationSequence.current) setBusy(null);
    }
  }

  async function selectSetupMode(mode: ComponentSetupMode) {
    if (!profileSaveAvailable || mode === "advanced") return;
    await mutate(
      `mode:${mode}`,
      async () => {
        await onboarding.saveComponentProfile({
          profile_id: "browser-first",
          mode,
          activate: true,
        });
        const [nextSetup, nextComponentSetup] = await Promise.all([
          updateAvailable
            ? setup.update({ current_step: "components" })
            : setup.status(),
          onboarding.componentSetup(),
        ]);
        setComponentSetupStatus(nextComponentSetup);
        return nextSetup;
      },
      `${MODE_LABELS[mode]} profile selected and persisted. The setup plan was recalculated.`,
    );
  }

  async function selectRegistryItem(card: SetupProductCard, selected: boolean) {
    if (!updateAvailable || card.kind !== "registry_item" || card.version === null) return;
    const ref = `${card.technical_id}@${card.version}`;
    const nextSelections = selected
      ? uniqueSelections([
          ...loadedStatus.registry_items,
          { item_id: card.technical_id, version: card.version },
        ])
      : loadedStatus.registry_items.filter((item) => registryRef(item) !== ref);
    await mutate(
      `registry:${ref}`,
      () => setup.update({ current_step: "components", registry_items: nextSelections }),
      "Component selection saved on the server. The dependency plan was recalculated.",
    );
  }

  async function setStep(step: Exclude<SetupStepId, "identity" | "ready">) {
    if (!updateAvailable) return;
    await mutate(
      `step:${step}`,
      () => setup.update({ current_step: step }),
      `Setup progress saved at ${STEP_LABELS[step]}.`,
    );
  }

  async function provision() {
    if (!provisionAvailable) return;
    const mutationId = mutationSequence.current + 1;
    mutationSequence.current = mutationId;
    snapshotEpoch.current += 1;
    setLoading(false);
    setBusy("provision");
    setError(null);
    setNotice(null);
    try {
      const result = await setup.provision();
      if (mutationId !== mutationSequence.current) return;
      snapshotEpoch.current += 1;
      setLoading(false);
      setStatus(result.setup);
      onStateChange?.(result.setup);
      if (result.outcome?.state === "failed") {
        setNotice(
          "Provisioning stopped after a failed owner-domain operation. "
          + "The failure is persisted and can be retried safely.",
        );
      } else {
        setNotice(
          result.replayed
            ? "The prior idempotent provisioning result was replayed."
            : "Selected installable components were provisioned through their canonical owner domains.",
        );
      }
    } catch (nextError) {
      if (mutationId !== mutationSequence.current) return;
      snapshotEpoch.current += 1;
      setLoading(false);
      setError(nextError);
    } finally {
      if (mutationId === mutationSequence.current) setBusy(null);
    }
  }

  async function validate() {
    if (!validateAvailable) return;
    await mutate(
      "validate",
      () => setup.validate(),
      "Readiness was recalculated from the live backend state.",
    );
  }

  const registryCards = loadedStatus.catalog.filter((card) => card.kind === "registry_item");
  const groupedCards = groupCards(loadedStatus.catalog);
  const actionableMutations = loadedStatus.plan.actions.filter(
    (action) =>
      (action.state === "pending" || action.state === "failed")
      && (action.kind === "install" || action.kind === "activate"),
  );
  const failedMutations = actionableMutations.filter((action) => action.state === "failed");
  const activeProfile = componentSetupStatus?.profiles.find(
    (profile) => profile.profile_id === componentSetupStatus.active_profile_id,
  ) ?? null;

  return (
    <div className="stack">
      <Card title="Guided initial setup">
        <p>
          Progress, selections and provisioning outcomes are server-owned and resumable. Browser
          reloads never restart completed work, and installation is performed only by explicit
          authenticated Control Plane mutations.
        </p>
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
        {notice ? <div className="state" role="status"><strong>{notice}</strong></div> : null}
        <SetupProgress status={loadedStatus} />
        <div className="actions">
          <button className="secondary" disabled={busy !== null || loading} onClick={() => void load()}>
            {loading ? "Refreshing…" : "Refresh setup state"}
          </button>
          {loadedStatus.current_step !== "ready" ? (
            <button
              className="secondary"
              disabled={busy !== null || !updateAvailable}
              onClick={() => void setStep(nextEditableStep(loadedStatus.current_step))}
            >
              Continue setup
            </button>
          ) : null}
        </div>
      </Card>

      <Card title="Setup mode">
        <p>
          Choose how the canonical component-profile service should select defaults for this
          deployment. The choice is persisted server-side and feeds the same dependency and
          compatibility plan shown below.
        </p>
        {!componentSetupAvailable ? (
          <p>The component-profile service is unavailable in this deployment.</p>
        ) : componentSetupStatus === null ? (
          <LoadingState label="Loading setup modes…" />
        ) : (
          <>
            <div className="grid-two">
              {componentSetupStatus.available_setup_modes.map((mode) => {
                const active = activeProfile?.mode === mode;
                const advanced = mode === "advanced";
                return (
                  <article className="state" key={mode}>
                    <div className="detail-header">
                      <strong>{MODE_LABELS[mode]}</strong>
                      {active ? <StatusBadge value="active" /> : null}
                    </div>
                    <p>{MODE_DETAILS[mode]}</p>
                    {advanced ? (
                      <p>
                        Use the Advanced component profile configuration below to provide explicit
                        defaults.
                      </p>
                    ) : (
                      <button
                        className={active ? "secondary" : "primary"}
                        disabled={busy !== null || !profileSaveAvailable || active}
                        onClick={() => void selectSetupMode(mode)}
                      >
                        {active ? "Selected" : `Use ${MODE_LABELS[mode]}`}
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
            <p>
              Active profile: <code>{componentSetupStatus.active_profile_id ?? "not selected"}</code>
            </p>
          </>
        )}
      </Card>

      <Card title="Applications & components">
        <p>
          Choose product capabilities rather than technical package IDs. Already available platform
          components are reused. Optional Registry items are selected here and remain unchanged until
          you explicitly provision the reviewed plan.
        </p>
        {Object.entries(groupedCards).map(([category, cards]) => (
          <section className="stack" key={category}>
            <h3>{CATEGORY_LABELS[category] ?? category}</h3>
            <div className="grid-two">
              {cards.map((card) => (
                <ProductCard
                  busy={busy !== null}
                  card={card}
                  key={card.id}
                  selected={
                    card.kind === "registry_item"
                    && card.version !== null
                    && selectedRegistryRefs.has(`${card.technical_id}@${card.version}`)
                  }
                  selectionAvailable={updateAvailable}
                  onSelected={(selected) => void selectRegistryItem(card, selected)}
                />
              ))}
            </div>
          </section>
        ))}
        {registryCards.length === 0 ? (
          <p>
            No optional Registry catalog is configured. The setup therefore reuses the shipped
            components and does not invent network or paid-provider installation choices.
          </p>
        ) : null}
      </Card>

      <Card title="Dependency & compatibility plan">
        <p>
          This preview is computed before mutation. Blocked and manual actions are never silently
          installed, while already available components are explicit no-op reuse actions.
        </p>
        <PlanTable status={loadedStatus} />
        <div className="actions">
          <button
            disabled={
              busy !== null
              || !provisionAvailable
              || actionableMutations.length === 0
            }
            onClick={() => void provision()}
          >
            {busy === "provision"
              ? "Provisioning…"
              : failedMutations.length > 0
                ? "Retry failed provisioning"
                : "Provision selected components"}
          </button>
          <button
            className="secondary"
            disabled={busy !== null || !validateAvailable}
            onClick={() => void validate()}
          >
            {busy === "validate" ? "Validating…" : "Validate readiness"}
          </button>
        </div>
        {loadedStatus.plan.blocking ? (
          <p role="alert">
            Blocked or manual-required actions keep final readiness incomplete. Independent safe
            pending actions may still be provisioned and retried individually.
          </p>
        ) : null}
        {actionableMutations.length === 0 ? (
          <p>No pending or failed automatic install or activation operation exists in the current plan.</p>
        ) : null}
      </Card>

      <Card title="Readiness">
        <dl className="definition-list">
          <div>
            <dt>Setup</dt>
            <dd><StatusBadge value={loadedStatus.readiness.ready ? "ready" : "incomplete"} /></dd>
          </div>
          <div>
            <dt>Canonical onboarding state</dt>
            <dd><code>{loadedStatus.readiness.canonical_onboarding_state ?? "unknown"}</code></dd>
          </div>
          <div>
            <dt>Active component profile</dt>
            <dd><code>{loadedStatus.active_profile_id ?? "not selected"}</code></dd>
          </div>
          <div>
            <dt>Dashboard</dt>
            <dd>
              {loadedStatus.readiness.dashboard_allowed
                ? "Available"
                : "Blocked until validation succeeds"}
            </dd>
          </div>
        </dl>
        {loadedStatus.readiness.blocking_actions.length > 0 ? (
          <p>Blocking actions: {loadedStatus.readiness.blocking_actions.join(", ")}</p>
        ) : null}
        {loadedStatus.readiness.dashboard_allowed ? (
          <div className="actions"><a href="/">Open dashboard</a></div>
        ) : null}
      </Card>
    </div>
  );
}

function SetupProgress({ status }: { status: SetupSessionStatus }) {
  return (
    <ol className="stack" aria-label="Initial setup progress">
      {status.steps.map((step) => (
        <li key={step.id}>
          <strong>{STEP_LABELS[step.id]}</strong>{" "}
          <StatusBadge value={step.state} />
        </li>
      ))}
    </ol>
  );
}

function ProductCard({
  card,
  selected,
  busy,
  selectionAvailable,
  onSelected,
}: {
  card: SetupProductCard;
  selected: boolean;
  busy: boolean;
  selectionAvailable: boolean;
  onSelected: (selected: boolean) => void;
}) {
  const selectable = card.kind === "registry_item"
    && card.version !== null
    && (card.install_status === "installable" || selected);
  return (
    <article className="state">
      <div className="detail-header">
        <div>
          <strong>{card.display_name}</strong>
          <p>{card.utility}</p>
        </div>
        <StatusBadge value={card.install_status} />
      </div>
      <dl className="definition-list">
        <div><dt>Compatibility</dt><dd><StatusBadge value={card.compatibility} /></dd></div>
        <div><dt>Recommendation</dt><dd><StatusBadge value={card.recommendation} /></dd></div>
        <div><dt>Delivery</dt><dd>{card.delivery}</dd></div>
        <div>
          <dt>Secrets</dt>
          <dd>{card.requires_secrets ? "Required during configuration" : "Not requested here"}</dd>
        </div>
      </dl>
      {card.dependencies.length > 0 ? <p>Dependencies: {card.dependencies.join(", ")}</p> : null}
      {card.blockers.length > 0 ? (
        <ul>{card.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
      ) : null}
      {selectable ? (
        <label>
          <input
            checked={selected}
            disabled={busy || !selectionAvailable}
            onChange={(event) => onSelected(event.target.checked)}
            type="checkbox"
          />{" "}
          Include in setup plan
        </label>
      ) : null}
      <details>
        <summary>Advanced details</summary>
        <dl className="definition-list">
          <div><dt>Technical ID</dt><dd><code>{card.technical_id}</code></dd></div>
          <div><dt>Version</dt><dd>{card.version ?? "—"}</dd></div>
          <div>
            <dt>License</dt>
            <dd>{card.license ?? "platform / not separately declared"}</dd>
          </div>
          <div><dt>Upstream</dt><dd>{card.upstream ?? "—"}</dd></div>
        </dl>
      </details>
    </article>
  );
}

function PlanTable({ status }: { status: SetupSessionStatus }) {
  if (status.plan.actions.length === 0) return <p>No setup actions are currently selected.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Component</th>
            <th>Action</th>
            <th>Status</th>
            <th>Owner</th>
            <th>Dependencies</th>
            <th>Blockers</th>
          </tr>
        </thead>
        <tbody>
          {status.plan.actions.map((action) => (
            <tr key={action.action_id}>
              <td><strong>{action.display_name}</strong></td>
              <td>{action.kind}</td>
              <td><StatusBadge value={action.state} /></td>
              <td>{action.owner}</td>
              <td>{action.dependencies.join(", ") || "—"}</td>
              <td>{action.blockers.join("; ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function groupCards(cards: SetupProductCard[]): Record<string, SetupProductCard[]> {
  const grouped: Record<string, SetupProductCard[]> = {};
  for (const card of cards) (grouped[card.category] ??= []).push(card);
  for (const values of Object.values(grouped)) {
    values.sort((left, right) => {
      const recommended = recommendationRank(left.recommendation)
        - recommendationRank(right.recommendation);
      return recommended !== 0 ? recommended : left.display_name.localeCompare(right.display_name);
    });
  }
  return grouped;
}

function recommendationRank(value: string): number {
  if (value === "recommended") return 0;
  if (value === "supported") return 1;
  if (value === "experimental") return 2;
  return 3;
}

function uniqueSelections(selections: SetupRegistrySelection[]): SetupRegistrySelection[] {
  return Array.from(
    new Map(selections.map((item) => [registryRef(item), item])).values(),
  ).sort((left, right) => registryRef(left).localeCompare(registryRef(right)));
}

function registryRef(selection: SetupRegistrySelection): string {
  return `${selection.item_id}@${selection.version}`;
}

function nextEditableStep(step: SetupStepId): Exclude<SetupStepId, "identity" | "ready"> {
  if (step === "identity" || step === "environment") return "components";
  if (step === "components") return "configuration";
  if (step === "configuration") return "validation";
  return "validation";
}
