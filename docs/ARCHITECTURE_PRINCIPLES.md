# Architecture Principles

These principles are binding unless changed by an explicit Architecture Decision Record (ADR). Implementations must not silently contradict them.

## 1. Platform-owned canonical model

Canonical public entities and lifecycle semantics belong to AI Multi-Agent Platform, not to an upstream framework. Adapters translate between platform contracts and backend-specific representations.

Canonical Task, Run and related platform identifiers are owned by the platform and remain stable across adapter replacement, retries, process restarts and node changes.

## 2. Replaceability by contract

Orchestrators, executors, model providers, tool providers, memory/file/knowledge backends, persistence systems, event transports, permission/policy services, automation backends, schedulers, observability backends and compute workers must be replaceable behind versioned platform-owned contracts.

A concrete integration must not require unrelated platform modules to import its private types or be rewritten when that integration is replaced.

## 3. No privileged upstream framework

Hermes may be the first orchestrator. Forge may provide execution or lifecycle capabilities. LiteLLM may provide model routing. MCP may provide tool interoperability. None of them is the platform itself.

The core remains testable without those systems installed or running.

## 4. Task-centric execution

The primary unit of work is a canonical Task and its Runs, not a chat session. Interactive chat may create or inspect work, but chat history is not the canonical lifecycle record.

The canonical conceptual lifecycle is:

`Goal -> Task -> Plan -> Steps/Subtasks -> Runs -> Artifacts -> Result`

Upstream framework concepts must map into this lifecycle rather than replace it.

## 5. Single-agent and multi-agent are both first-class

The platform supports workloads executed by one agent as well as coordinated teams of agents. Multi-agent orchestration must not be required for simple single-agent tasks, and single-agent execution must not require a separate architecture.

## 6. Single-node is a first-class production topology

Single-node operation is a valid production topology, not a development-only special case.

Local-only and distributed operation share the same Node, Worker and Job concepts. Multi-node mode extends the same domain contracts rather than introducing a separate architecture. Scheduling is capability-based rather than tied to hostnames, vendors, VPS classes or operating locations.

## 7. Deployment and hardware neutrality

Canonical contracts must not depend on VPS hosting, cloud deployment, Kubernetes, KVM, a specific GPU vendor, a specific operating system or another concrete infrastructure topology.

Hardware and deployment characteristics are capabilities and metadata, not canonical identities.

## 8. Explicit ownership of durable state

For every lifecycle state there is one canonical owner. New workflow engines, execution backends, databases or orchestrators must not introduce competing lifecycle authorities.

Canonical platform state is represented through platform-owned domain contracts even when payloads or execution state are persisted by replaceable backends.

## 9. API-first boundaries

Frontend, CLI, automations and external applications communicate through canonical platform APIs and events. They do not directly depend on Hermes, Forge, model providers or other backend implementations.

Core APIs must not expose backend-private request/response types as canonical public contracts.

## 10. Local-first model/provider baseline

The platform is model- and provider-agnostic. Model assignments may vary per agent, task, step or capability/policy scope without changing the canonical domain model.

The baseline runs self-hosted without mandatory recurring paid AI/API services. Local and self-hosted models are first-class options. Optional external or paid providers remain optional adapters.

## 11. Capability discovery over hard-coded identity

Agents and schedulers should select models, tools, workers and nodes from declared capabilities and policy where practical instead of binding to concrete names in core logic.

## 12. Security, approvals, traceability and recovery are cross-cutting

Identity, authorization, policy evaluation, approval gates, auditability, observability, traceability and recovery belong at canonical platform boundaries rather than only inside individual integrations or the UI.

Sensitive operations must be representable as governed actions that can require explicit human or policy approval.

## 13. Provenance and licensing are first-class

Any reused or vendored upstream code retains provenance and compatible licensing. Dependencies, external services, protocols and vendored code are treated as distinct integration categories.

Third-party source reuse must follow the repository's license and provenance policy.

## 14. Evidence-driven evolution

Major architectural dependencies require contract tests, reference implementations and, where useful, isolated evaluation work before becoming production assumptions. Existing work is reused only when it satisfies the platform-owned contracts.

## Explicit architecture invariants

The following invariants are reviewable constraints for implementation work:

1. No upstream project is the canonical platform domain model.
2. Canonical task/run identifiers are platform-owned.
3. Core APIs must not expose backend-private request/response types.
4. Replacing one adapter must not require unrelated core modules to be rewritten.
5. Single-node mode is a valid production topology, not a development-only special case.
6. Multi-node mode extends the same domain contracts rather than introducing a separate architecture.
7. Paid external AI APIs may be supported, but are optional.
8. Baseline operation must remain possible with local/self-hosted components.
9. Third-party source reuse must follow the repository's license and provenance policy.
10. Security, approvals, traceability and recovery are cross-cutting platform requirements.

## Replaceable architecture boundaries

Platform-owned contracts must preserve replaceability for:

- Orchestration
- Execution / lifecycle backend
- Model providers and model routing
- Tools and capability providers
- Memory
- Files / object storage
- Knowledge / retrieval
- Persistence
- Events / messaging
- Authorization / policy
- Nodes / workers / scheduling
- Observability backend
- Automation / trigger backend
- Optional durable workflow engine

## Architecture decisions

Concrete implementation choices and deliberate refinements to this baseline are documented as Architecture Decision Records under [`docs/adr/`](adr/README.md).

An ADR may explain how a principle is implemented or deliberately evolve a decision, but implementation work must never create an undocumented contradiction with this document or [`PRODUCT_VISION.md`](PRODUCT_VISION.md).
