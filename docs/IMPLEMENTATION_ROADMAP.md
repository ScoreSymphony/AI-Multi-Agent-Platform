# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-03

This roadmap defines the recommended implementation order for the AI Multi-Agent Platform from the current repository state toward the ideal end product.

The roadmap is a point-in-time planning view. GitHub issue state and the target issue's current wording remain the source of truth for whether work is complete and which hard dependencies apply. Re-check them immediately before starting implementation.

Issue numbers are identifiers only. The actual order is determined by canonical ownership, explicit hard dependencies, contract stability, unlock value and safe parallelization.

The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

## 1. Execution rules

### 1.1 Hard dependencies are blockers

An issue may start when its explicit hard dependencies are implemented/merged sufficiently for the target work to consume their canonical contracts.

`Follow-up integrations`, related issues, progressive integrations and future extension references are not blockers unless the target issue explicitly says otherwise.

### 1.2 Closed issue state is the roadmap completion signal

For this roadmap, a substantive issue is treated as completed when it is closed on GitHub.

Issue-body checkboxes may describe progressive integrations, historical acceptance work or extracted follow-ups and can lag behind the actual issue state. Conversely, an open issue can already contain a substantial foundation. Do not infer completion from checkboxes alone.

### 1.3 Contracts merge before consumers

When parallel branches depend on a shared contract, prefer this merge order:

1. canonical contract/domain change;
2. reference implementation + contract tests;
3. adapters/providers;
4. Control Plane/API extension;
5. UI/CLI/client integration;
6. deployment/operations integration.

### 1.4 Parallel work must preserve ownership

Parallelization is valid only when branches do not independently redefine the same canonical concept.

Examples:

- #33 owns canonical Agent/AgentTeam definitions; #8 Hermes must consume them.
- #36 authenticates actors; #15 remains authorization authority.
- #14 owns Node/Worker scheduling semantics; #39 packages them into deployment profiles.
- #75 owns Notifications; #76/#171 provide accounting events/integration rather than a second notification model.
- #87 owns Organization/Team/Membership semantics; #157 consumes those semantics for cross-scope Task moves.

### 1.5 Progressive issues may start before all follow-up domains exist

Issues such as #38, #39 and #45 deliberately have an early usable stage plus later integrations. Their hard dependencies determine when implementation may begin; later domains determine when all progressive acceptance criteria can be satisfied.

---

# 2. Completed platform baseline on `main`

The repository has progressed well beyond the original #1–#7 foundation.

## Canonical architecture and execution foundation

Completed:

- #1 — product vision and architecture principles
- #2 — repository structure, CI and contribution baseline
- #3 — upstream/license/provenance policy
- #4 — canonical platform domain model
- #5 — replaceable core interfaces/contracts
- #6 — platform-owned Task/Run/Event kernel
- #7 — execution abstraction and deterministic reference executor

## Models, capabilities, data and reusable execution work

Completed:

- #9 — Forge reuse audit and selective execution-port work
- #10 — ModelProvider, Model Registry and Model Router
- #11 — optional LiteLLM adapter
- #12 — Capability Registry and MCP adapter
- #13 — persistence, Files, scoped Memory and Knowledge boundaries

## Security, control and platform foundations

Completed:

- #15 — identity, authorization, permissions and approvals
- #16 — progressive end-to-end observability foundation/integrations
- #17 — initial API-first Web UI shell and core Task/Run frontend
- #32 — versioned Control Plane and Task/Run API foundation
- #34 — configuration, credentials and secrets management
- #35 — event transport and internal messaging architecture
- #37 — Workspace and project-environment management
- #43 — threat model and security-hardening baseline
- #73 — canonical Terminal and execution-session interface

## Product/accounting work already completed

Completed:

- #76 — canonical usage/resource accounting foundation and implemented progressive accounting scope
- #88 — practical Task management: priorities, deadlines, assignment and dependencies

Important extracted follow-ups:

- #157 owns canonical Task Project reassignment/move semantics; it was intentionally kept out of #88 because `Task.project_id` is canonical scope, not ordinary planning metadata.
- #171 owns post-#76 Workspace/Organization/Notification accounting integrations that depend on later domain semantics.

These completed issues materially change the execution frontier below.

---

# 3. Current execution frontier — highest-leverage work

The five highest-leverage open issues can now be worked in parallel because all of their hard dependencies are closed.

## Lane A — Authentication

### #36 — Authentication and session management

Ready now because #15 and #32 are complete.

Why prioritize it:

- unlocks #44 Connectors;
- unlocks #75 Notifications;
- unlocks #87 Organizations/Memberships;
- unlocks the initial production deployment baseline in #39;
- combines with #33 to unlock #72 Chat;
- enables the remaining authentication-sensitive CLI work in #38.

This is currently one of the strongest critical-path issues.

---

## Lane B — Canonical Agents and Agent Teams

### #33 — Agent definitions, profiles and team runtime

Ready now because #4, #5, #10, #12, #13 and #15 are complete.

Why prioritize it:

- unlocks #8 Hermes;
- unlocks #77 Standard Agents/Teams;
- unlocks #86 runtime Verification/Review;
- combines with #36 to unlock #72 Chat;
- contributes to the gates for #78 Templates and #79 Import/Export;
- enables Agent metadata/inspection completion in #38.

Required direction remains:

`Canonical Agent/Team -> orchestrator adapter mapping`

not:

`Hermes/private orchestrator Agent -> canonical Agent`

---

## Lane C — Distributed compute

### #14 — Node registry, Worker protocol and capability-based scheduler

Ready now because #4, #5, #7, #10, #12, #15 and #37 are complete.

Why prioritize it:

- establishes the real single-node/multi-node shared scheduling path;
- unlocks the remaining hard dependency for #21 together with #18;
- enables distributed deployment profiles in #39;
- supplies Node/Worker administrative surfaces needed for full #38 completion;
- provides the distributed runtime needed by final conformance.

#14 must consume the existing Workspace, authorization, model, capability and execution contracts rather than redefine them.

---

## Lane D — Automations

### #18 — Automations, triggers and event-driven Task creation

Ready now because #4, #6, #15 and #32 are complete.

Why prioritize it:

- unlocks #21 together with #14;
- contributes to #78 Templates;
- contributes to #79 portable Import/Export;
- establishes schedule/webhook/event-driven work through the normal canonical Task lifecycle.

Core invariant:

`Trigger -> Automation evaluation -> canonical Task creation -> normal platform lifecycle`

---

## Lane E — Plugin runtime

### #20 — Plugin system and extension registry

Ready now because #3, #4, #5, #12 and #15 are complete.

Why prioritize it:

- contributes to #78 Templates and #79 Import/Export;
- is required for #81 optional Registry/Marketplace;
- is required for #40 Backup/Restore and #41 Upgrade/Migration paths;
- is required for #42 release/upstream compatibility work.

Plugins must extend platform contracts, never become a parallel architecture or security bypass.

---

# 4. Additional work that is already ready now

These issues are not blocked by the five lanes above and may run concurrently when capacity allows.

## #19 — Evaluation and regression framework

All hard dependencies (#4, #6, #10, #12, #16) are complete.

This is useful now because it begins producing evidence before more adapters and product features accumulate. It also removes one future blocker for #42.

## #45 — Platform-wide Search and resource discovery

Stage 1 is ready because its only hard dependencies, #15 and #32, are complete.

Do not wait for every later searchable domain. Start with currently available canonical resources and add Agents, Workers, Connectors, Conversations, Verification and other resources progressively.

## #74 — Replaceable Browser/Web capability

Ready because #7, #12, #13 and #15 are complete.

Build against canonical Capability/File/Security boundaries; #14 later adds Worker placement and #34/#43 already provide the secret/security foundations it consumes.

## #38 — CLI and administrative control interface

#38 remains open, but its foundational CLI and much of its current functionality already exist. Its hard dependency #32 is complete, so further work may continue now.

Do not force premature closure: full completion still needs progressive integration with domains such as #36 Authentication, #14 Nodes/Workers and #33 Agents/Teams. Treat #38 as a continuing client lane rather than a blocker for those backend contracts.

---

# 5. Convergence Gate A — after #36 Authentication

Once #36 is complete, four major platform/product areas become directly available in parallel.

## #44 — Connector and external-integration framework

Hard path now:

`#36 -> #44`

All other hard dependencies (#5, #12, #13, #15, #34) are already complete.

After #44, #82 Repository/Git becomes available.

## #75 — Notifications and user-attention system

Hard path now:

`#36 -> #75`

All other hard dependencies (#6, #15, #32) are already complete.

#75 later combines with #87 to unlock the remaining #171 accounting integrations.

## #87 — Organization, Team and Membership management

Hard path now:

`#36 -> #87`

All other hard dependencies (#15, #32) are complete.

After #87 stabilizes:

- #157 can define safe cross-scope Task Project moves against real ownership/sharing semantics;
- #171 can integrate real Organization/Team accounting once #75 is also available.

## #39 — Deployment profiles and self-hosted installation

The initial single-node production profile becomes ready after #36 because #32, #34, #16 and #4–#7 are already complete.

Important: #39 must **not** wait for #14 to begin its single-node baseline. #14 is a progressive dependency for distributed Worker profiles, not a blocker for the first supported production deployment.

---

# 6. Convergence Gate B — after #33 Agents/Teams

Once #33 is complete, several major features become directly available.

## #8 — Hermes orchestrator adapter

Hard path now:

`#33 -> #8`

All other hard dependencies (#3–#7, #10, #12) are already complete.

Hermes must consume canonical Agent/Team/Task contracts and remain optional.

## #77 — Editable Standard Agents and starter Teams

Hard path now:

`#33 -> #77`

All other hard dependencies (#10, #12, #15) are complete.

These remain ordinary canonical definitions, not mandatory platform roles.

## #86 — Runtime Task Verification, Review and completion policies

Hard path now:

`#33 -> #86`

All other hard dependencies (#4, #6, #13, #15, #32) are complete.

Verification must stay distinct from:

- #15 security Approval; and
- #19 regression Evaluation.

## #72 — Task-centric Chat

Requires both current frontier lanes:

`#33 + #36 -> #72`

All other hard dependencies (#10, #13, #15, #32) are complete.

Chat remains an interaction surface; durable work still becomes canonical Tasks.

---

# 7. Convergence Gate C — orchestration packages, portability and workflow decision

## #21 — Durable workflow-engine decision

Remaining hard path:

`#14 + #18 -> #21`

All other hard dependencies (#1, #4, #5, #6, #9) are complete.

This remains an evidence/ADR issue. Do not introduce Temporal or another workflow engine as a production dependency before this decision is made.

## #78 — Reusable Templates

Remaining hard path:

`#33 + #18 + #20 -> #78`

All other hard dependencies (#10, #12, #15, #37) are complete.

## #79 — Portable Import/Export

Remaining hard path:

`#33 + #18 + #20 -> #79`

All other hard dependencies (#4, #13, #15) are complete.

Operational backup/restore remains separate in #40.

## #81 — Optional Registry / Marketplace

Path:

`#20 + #78 + #79 -> #81`

#3 and #15 are already complete.

The Registry remains optional and must not become a baseline dependency.

---

# 8. Connector and collaboration follow-ups

## #82 — Provider-neutral Repository/Git integration

Remaining hard path:

`#36 -> #44 -> #82`

#37, #12, #13, #15 and #34 are already complete.

Local Git remains the self-hosted baseline; hosted providers are connector implementations.

## #157 — Canonical Task Project reassignment/move

Implement after #87's sharing/ownership semantics are stable enough to define cross-scope compatibility.

#157 must preserve historical Task/Run/Event provenance and cannot be implemented as frontend-only or metadata-only reassignment.

## #171 — Post-#76 accounting integrations

Remaining integration gate:

`#36 -> (#75 + #87) -> #171`

#37 and the canonical #76 accounting core are already complete.

#171 finishes:

- Workspace/snapshot accounting semantics against #37;
- real Organization/Team accounting visibility against #87;
- budget-threshold Notification delivery through #75.

---

# 9. Deployment, recovery and release lifecycle

## #39 — Deployment profiles

As noted above, the single-node baseline begins after #36.

Distributed profiles progressively consume #14/#35. Optional HA packaging later consumes #89.

## #40 — Backup, Restore and disaster recovery

Remaining hard path:

`#20 + #38 + #39 -> #40`

#6, #13 and #34 are already complete.

This means #38 should be finished before #40 is treated as complete operational tooling.

## #41 — Database migrations and platform upgrade lifecycle

Remaining hard path:

`#20 + #40 + #79 -> #41`

#13 and #32 are already complete.

## #42 — Release, update and upstream synchronization

Remaining hard path:

`#8 + #19 + #20 + #41 -> #42`

#3, #9 and #11 are already complete.

Compatibility claims must be based on actual adapter, migration and regression evidence.

## #89 — Optional Control Plane HA/failover

Remaining hard path:

`#36 + #39 + #40 -> #89`

#6, #13, #32 and #35 are already complete.

HA is optional. Single-node production must remain valid with all HA components disabled.

---

# 10. Final cross-platform acceptance

## #46 — End-to-end Conformance and Platform Acceptance Suite

#46 is the final system acceptance gate.

It consumes the completed platform domains and validates representative paths across:

- canonical lifecycle and recovery;
- reference-only local operation;
- Hermes and Forge adapter paths;
- local/self-hosted model and capability paths;
- distributed Workers;
- authorization and Approvals;
- runtime Verification;
- Authentication;
- Workspaces/Artifacts;
- Automations;
- UI/CLI consistency;
- Chat;
- Terminal;
- Browser;
- Notifications;
- Usage/Resources;
- Organizations/Memberships;
- practical Task management;
- Templates and portable Import/Export;
- Repository/Git;
- Registry-disabled and Registry-enabled paths;
- backup/migration/release compatibility;
- optional HA/failover while preserving the HA-disabled baseline.

No earlier issue should redefine canonical contracts merely to make #46 easier to pass.

---

# 11. Recommended parallel allocation

## If five implementation agents are available

This is the recommended highest-leverage allocation now:

| Agent | Issue | Primary unlock |
|---|---|---|
| A | #36 Authentication | #44, #75, #87, #39, part of #72 |
| B | #33 Agents/Teams | #8, #77, #86, part of #72/#78/#79 |
| C | #14 Nodes/Workers | distributed runtime, #21, distributed #39 |
| D | #18 Automations | #21, #78, #79 |
| E | #20 Plugins | #78, #79, #81, #40/#41/#42 chain |

These five lanes attack the largest remaining dependency bottlenecks simultaneously.

## If more capacity is available

Add independent lanes in roughly this order:

- #19 Evaluation/Regression;
- #45 Search foundation;
- #38 CLI progressive completion;
- #74 Browser.

The exact order among these secondary lanes may be adjusted to product priorities because they do not currently unlock as many downstream hard dependencies as #36/#33/#14/#18/#20.

## Merge discipline

Do not merge downstream compatibility hacks ahead of their owning contracts.

Examples:

- merge #33 canonical Agent contracts before #8 Hermes mappings;
- merge #36 actor/session contracts before #44/#75/#87 authentication integrations;
- merge #14 Worker contracts before distributed deployment/client assumptions;
- merge #20 plugin lifecycle before #81 distribution logic;
- merge #87 ownership/sharing semantics before #157 cross-project move semantics.

---

# 12. Current dependency map

```text
COMPLETED BASELINE
#1-#7, #9-#13, #15-#17,
#32, #34, #35, #37, #43, #73, #76, #88
        |
        +------------------------------------------------------------+
        |              |              |              |              |
       #36            #33            #14            #18            #20
   Authentication   Agents/Teams   Nodes/Workers   Automation      Plugins
        |              |              |              |              |
        |              |              +------+#18---+              |
        |              |                     |                      |
        |              |                    #21                     |
        |              |                                             |
        |              +--> #8 Hermes                                |
        |              +--> #77 Standard Agents                      |
        |              +--> #86 Verification                         |
        |              +--------------------+                        |
        |                                   |                        |
        +-------------------------------> #72 Chat                   |
        |                                                            |
        +--> #44 Connectors --> #82 Repository/Git                    |
        +--> #75 Notifications ----+                                  |
        +--> #87 Orgs/Members -----+--> #171 accounting integrations |
        |       |                                                    |
        |       +--> #157 Task project moves                         |
        |                                                            |
        +--> #39 Deployment --> #40 Backup --> #41 Upgrade --> #42 Release
                                  ^              ^             ^
                                  |              |             |
                                #20            #79        #8 + #19 + #20
                                                ^
                                                |
                                   #33 + #18 + #20
                                                |
                                               #79

#33 + #18 + #20 --> #78 Templates
#20 + #78 + #79 --> #81 optional Registry
#36 + #39 + #40 --> #89 optional HA

READY IN PARALLEL NOW:
#19 Evaluation, #45 Search, #74 Browser, progressive #38 CLI

FINAL:
#46 End-to-End Platform Acceptance
```

---

# 13. Critical-path summary

The remaining work no longer has one simple linear chain. The most important converging chains are:

```text
#36 -> #39 -> #40 -> #41 -> #42 -> #46
#33 -> #8 ---------------------> #42 -> #46
#20 -> #40/#41/#42 ------------------> #46
#33 + #18 + #20 -> #79 -> #41 -> #42 -> #46
#14 + #18 -> #21
#36 -> #44 -> #82 --------------------> #46
#36 -> #87 -> #157 -------------------> #46
#36 -> #75 + #87 -> #171 ------------> #46
```

The best immediate strategy is therefore **not** to resume the old model/tool/data/control-plane wave. Those foundations are already complete. The current bottlenecks are Authentication, canonical Agents/Teams, distributed Workers, Automations and Plugins, with Evaluation/Search/Browser/CLI able to advance in parallel.