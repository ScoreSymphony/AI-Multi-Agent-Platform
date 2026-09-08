# Integration review: active branches 2026-09-09

## Scope

Reviewed `integration/active-branches-2026-09-08` against `main`, with emphasis on:

- frontend ↔ backend contracts;
- Control Plane composition and manifest exposure;
- Goals;
- Registry / Marketplace and the technical catalog;
- duplicated, stale or contradictory implementations introduced by merged issue branches.

At review start the integration branch was 158 commits ahead of `main` and 0 commits behind. The reviewed integration head was `a4fb504375da940a6b2552037783da04a7499c15`.

## Findings fixed on this branch

### Automation / Goal observation task optionality

The runtime model already allows a successful `TriggerDelivery` with `generated_task_id=None`, and the Goal runtime intentionally uses that state when a Goal observation performs a review but no executable work is required.

The older Automation callback contract still declared `TaskCreator -> Awaitable[str]`. The composed Goal Control Plane therefore had to cast an optional generated task id to `str`, even though the runtime result remained `None`.

A first integration fix widened `TaskCreator` to `Awaitable[str | None]`. Review correctly identified that this was too permissive: an ordinary Automation embedding could then return `None` accidentally and still satisfy the static callback contract.

The final fix preserves the ordinary Automation invariant and makes the Goal exception explicit:

- `TaskCreator` remains `Awaitable[str]`;
- `NO_TASK_REQUIRED` is an explicit, platform-owned result signal for a handled delivery that legitimately needs no canonical Task;
- `AutomationService` maps only that explicit signal to `generated_task_id=None`;
- a missing, non-string, or blank ordinary TaskCreator result is treated as a contract violation rather than a successful delivery;
- the Goal composition uses `NO_TASK_REQUIRED` only after `dispatch_goal_automation_delivery()` has explicitly handled the delivery and returned no generated Task;
- the ordinary Control Plane automation task-creation seam remains `-> str`.

Regression coverage now proves both sides of the contract: an ordinary creator returning `None` fails, while the explicit no-task signal succeeds without fabricating a Task. The existing Goal integration test continues to prove the composed monitoring-only Goal behavior.

## Verified contract alignment

### Goals

The frontend `GoalClient` and backend Goal Control Plane agree on:

- resource collection: `goals`;
- commands: `goal.create`, `goal.activate`, `goal.pause`, `goal.resume`, `goal.cancel`, `goal.fail`, `goal.revise`, `goal.review`, `goal.attach-task`, `goal.record-task-outcome`;
- Goal status and progress enum values;
- criterion kinds/operators and task-link states;
- revision-aware mutations;
- nullable/optional review output where no task is generated.

`Shell.tsx` gates both Goal routes through the Control Plane manifest resource `goals`. The composed Control Plane registers Goals as canonical product state, so the manifest exposes the collection and commands when that composition is used.

The existing Goal runtime integration test already covers the important no-fabricated-task behavior: a monitoring-only Goal review succeeds with `generated_task_id is None`.

### Browser authentication / CSRF

Specialized frontend clients do not independently implement the browser session boundary, but `Shell.tsx` supplies `BrowserSessionClient.fetch` to them. That wrapper preserves cookie credentials and injects the CSRF token for unsafe methods. No concrete browser authentication regression was found in Goals, Evaluation or Registry clients in the reviewed composition.

### Marketplace / Registry

The Marketplace frontend and distribution Control Plane agree on:

- collection: `registry-items`;
- list filters used by the UI (`item_type`, `trust_status`, `tag`, `category`, `license`, `publisher`, `required_capability`, `platform_version`, `technical_component`, `update_available`);
- preview / activate / pin / unpin command shapes;
- preview ids (`item_id@version`);
- installed and update state used by the UI.

The shipped single-node adapter registers the Registry only when `registry_catalog` is configured. The frontend correctly gates Marketplace rendering on the manifest resource `registry-items`, so an unconfigured Registry is represented as unavailable rather than assumed to exist.

Backend technical metadata is fail-closed for structured technical tags. The local/filesystem Registry path validates unsupported or conflicting structured values before exposing them as technical catalog data.

## Remaining integration risks

### 1. Frontend HTTP transport is duplicated across domain clients — medium

`ControlPlaneClient`, `ControlPlaneCollectionClient`, `GoalClient`, `EvaluationClient`, `RegistryClient` and other domain clients duplicate parts of request construction, correlation/idempotency headers and error normalization.

This is not currently breaking the shipped browser path because all clients receive `BrowserSessionClient.fetch`, but it creates a high-drift surface: future authentication headers, retry semantics, error-body changes or observability headers must be updated in multiple clients.

Recommended follow-up: expose one reusable Control Plane transport/command primitive and make domain clients thin typed adapters around it.

### 2. Technical Marketplace taxonomy is duplicated in Python and TypeScript — medium

`distribution/technical_catalog.py` is the strict backend authority for technical categories and structured tag vocabularies. `frontend/src/marketplace/technical.ts` repeats the category list and tag parsing logic.

The backend currently protects the shipped catalog from malformed technical metadata, so this is not an immediate data-integrity bug. It is still a contract-drift risk: a new category/status added to one side can produce different UI interpretation until the second copy is updated.

Recommended follow-up: expose a canonical technical metadata projection in the Registry resource or generate/share the taxonomy from a schema rather than maintaining two hand-written vocabularies.

### 3. Frontend technical parsing is more permissive than backend parsing — medium

The backend rejects conflicting or unsupported structured technical tags. The frontend helper currently sorts multiple single-value tags and picks one, and it does not validate all structured values against the backend vocabulary.

With the validated local/filesystem provider this mismatch is masked. It becomes material if another Registry provider, import path or future API surface can supply Registry items without the same validation boundary.

Recommended follow-up: either consume server-derived technical metadata or make the frontend parser fail closed with the same vocabulary rules.

### 4. Control Plane composition relies on a deep inheritance/registration chain — medium

Goals are registered implicitly by `approval_portability_composition.ControlPlane`; Registry is registered later by the outer single-node adapter when configured; other domains register collections and commands at different composition layers.

The manifest correctly reflects the final registered state, but MRO/order changes can silently change which routes are present or which override handles a command.

Recommended follow-up: add one composition-level manifest/OpenAPI contract test for the shipped `build_default_single_node_deployment`, including a configured Registry catalog and Goals.

### 5. Domain-specific frontend clients do not share compile-time contracts with backend models — medium

Goal and Registry TypeScript interfaces currently match the reviewed Python projections, but they are manually mirrored. The integration branch adds many domain surfaces, making manual synchronization increasingly fragile.

Recommended follow-up: generate stable frontend DTO types from the canonical OpenAPI/schema surface, while keeping UI view models separate from transport DTOs.

### 6. Full branch test execution remains a merge gate — high until green

The previous PR head completed all repository workflows successfully. Because the review follow-up changed the Automation contract implementation and added regression coverage, the final head must complete the same required CI/type/frontend checks again before merge.

## Review conclusion

No blocking endpoint or enum mismatch was found in the reviewed Goals or Marketplace UI paths. The concrete contradiction found between Automation and Goal observation semantics has been corrected without weakening the ordinary Automation Task-creation contract. The highest remaining architectural risks are contract duplication rather than a currently demonstrated runtime break: frontend transport duplication, duplicated Marketplace taxonomy/parsing, and implicit Control Plane composition order.
