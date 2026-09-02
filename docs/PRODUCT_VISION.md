# Product Vision

## Purpose

AI Multi-Agent Platform is a general-purpose platform for defining, orchestrating, executing, observing and evaluating AI-assisted work across one or many machines.

It is not a ScoreSymphony-specific product. ScoreSymphony or any other domain application may consume the platform through its public APIs and extension mechanisms without becoming part of the platform core.

The platform supports both single-agent and multi-agent workloads. Multi-agent coordination is an optional capability of the platform, not a requirement for every task.

## Ideal end state

A user can express a goal and have the platform turn it into canonical tasks, plans, steps, runs, artifacts and results. Multiple agents may cooperate dynamically. Models, tools, execution backends and compute nodes can be selected or replaced according to capabilities, policy and configuration.

The same platform should work on a single local computer or across heterogeneous nodes such as desktops, notebooks, workstations, home servers, VPS instances, cloud VMs, GPU servers, NAS devices or other reachable machines. No hardware class is privileged in the domain model.

Single-node operation is a first-class production topology. Distributed multi-node execution extends the same platform-owned contracts rather than introducing a separate architecture.

The platform is deployment-neutral. Its architecture must not require VPS hosting, public cloud, Kubernetes, KVM, a specific GPU vendor, a specific operating system or another single deployment environment.

## Canonical workflow

Goal -> Task -> Plan -> Steps/Subtasks -> Runs -> Artifacts -> Result

The platform owns the canonical identities and lifecycle semantics needed to trace this flow. External frameworks may implement parts of it but must not redefine the public platform model.

Related canonical concepts include at minimum:

- Project / Workspace
- Agent / Agent Team
- Task / Plan / Step
- Run / Agent Run / Worker Job
- Artifact / Result
- Event
- Approval
- Tool / Capability
- Model assignment
- Node / Worker
- Files / Memory / Knowledge references

## Major product areas

- Home and activity overview
- Chat/interactive goal entry
- Tasks and runs
- Projects
- Agents and agent teams
- Automations and triggers
- Files
- Knowledge
- Memory
- Tools and integrations
- Models and model routing
- Compute: nodes, workers, jobs and resources
- Evaluations
- Events
- Approvals
- Observability
- Identity, permissions and security
- Settings
- Plugin/extension registry
- Import/export and reusable templates

## Replaceable architecture layers

The architecture must define platform-owned boundaries that allow multiple implementations for at least:

1. Orchestration
2. Execution / lifecycle backend
3. Model providers and model routing
4. Tools and capability providers
5. Memory
6. Files / object storage
7. Knowledge / retrieval
8. Persistence
9. Events / messaging
10. Authorization / policy
11. Nodes / workers / scheduling
12. Observability backend
13. Automation / trigger backend
14. Optional durable workflow engine

Hermes, Forge, LiteLLM, MCP implementations, storage products, schedulers, workflow engines and similar projects are candidates for adapters or reusable components, not permanent definitions of these subsystems.

Replacing one adapter or implementation must not require unrelated core modules to be rewritten.

## Model and provider principles

The platform is model- and provider-agnostic. Models can be assigned per agent, task, step or capability/policy scope through platform-owned contracts.

Local and self-hosted models are first-class options. Optional paid providers may be supported through adapters, but they must never be required for baseline operation.

## API and client principles

The platform is API-first. A web UI, CLI, automation clients and external applications consume canonical platform APIs and events rather than backend-private interfaces.

External systems integrate through replaceable adapters, plugins or protocol providers. Core APIs must not expose backend-private request/response types as canonical platform contracts.

## State, governance and safety principles

Canonical platform state remains inside platform-owned domain contracts. External frameworks may hold implementation state, but their identifiers and private lifecycle concepts do not replace canonical platform identities.

Human approvals and policy boundaries are first-class platform concepts for sensitive operations.

Observability, auditability, traceability, security and recovery are architectural concerns across the platform rather than UI-only features.

## Deployment and cost principles

The default baseline must be self-hostable and local-first. It must be possible to operate the platform without required recurring paid AI/API services. Optional paid providers may be supported through adapters, but they must never be necessary for baseline operation.

## Product quality goals

The final system should be:

- extensible without repeated core rewrites;
- testable with reference/fake implementations;
- observable end to end;
- recoverable after failures;
- secure by explicit permissions and approvals;
- auditable through canonical events and identifiers;
- portable across deployment environments;
- understandable enough that upstream integrations can be added or removed without losing architectural ownership.

## Normative architecture and ADRs

This document and [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md) define the normative product and architecture baseline.

Concrete implementation choices may refine these principles only through explicit architecture decisions. Architecture Decision Records live under [`docs/adr/`](adr/README.md). ADRs may select implementations or refine contracts, but they must not silently contradict the platform identity, canonical ownership model or replaceability requirements defined here.
