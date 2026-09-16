# Frontend hotspot regression audit (#1160 / #985)

This document records the completion audit for the frontend responsibility decomposition introduced by #985. It is retained evidence for #1160 and maps the affected features to the regression classes required by the original issue.

The audit deliberately reuses existing tests where they already prove the required responsibility boundary. It does not impose identical file layouts or artificial test-count targets.

## Coverage matrix

| Feature | Page / route regression | Extracted state / mapping logic | Interactive component / form coverage | Accessibility / keyboard regression | Loading / error / empty states | Transport / server ownership evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Agent Configuration | `frontend/src/pages/AgentConfigurationPage.test.tsx` covers create composition and edit loading boundary | Existing validation/state tests in `AgentConfigurationPage.test.tsx` and feature-local state modules | Create Agent and Agent Team editors are rendered through their canonical extracted fields/components | Native labeled controls and native buttons remain in the rendered editor; representative interaction semantics are covered elsewhere in the matrix | Edit loading is covered; empty is not a meaningful state for the editor; canonical load errors continue through `ErrorState` | Uses `ControlPlaneClient`, `ConfigurationClient` and `ControlPlaneCollectionClient`; no inline generic HTTP was introduced |
| Automations | `frontend/src/pages/automations/AutomationsPage.test.tsx` covers route composition | Existing `automationDraft.test.ts` covers draft validation/mapping | `AutomationForm` and `DeliveryTable` behavior covered in `AutomationsPage.test.tsx` | Native submit/retry controls, labels and disabled state are asserted | `AutomationInventoryState` covers loading, backend error, empty and populated rendering | `frontend/src/api/automations.test.ts` proves canonical commands through the domain client; generic transport remains `ApiTransport` |
| Templates | Existing `frontend/src/pages/TemplatesPage.test.tsx` plus `frontend/src/pages/templates/TemplatesPage.regression.test.tsx` | Existing `templateCreation.test.ts` and canonical resource-link tests | Route regression renders both extracted creation forms | Native form semantics are preserved; no custom keyboard interception is introduced by the decomposition | `TemplateLibraryState` covers loading, backend error and empty library rendering | `TemplateClient` remains the feature boundary and transport check remains repository-wide |
| Organizations | `frontend/src/pages/organizations/OrganizationsPage.test.tsx` covers route composition | Existing `collaborationContext.test.ts` plus new `organizationFormInput.test.ts` cover UI context and form normalization | Organization summary, team selection, membership assignments and invitations are covered | Labeled role/policy controls, native team buttons, disabled lifecycle actions and invitation actions are asserted | Route loading, backend/mutation errors and personal-scope empty state are covered | `frontend/src/api/organizations.test.ts` proves canonical OrganizationClient routes/commands; canonical organization state remains server-owned |
| Goals | Existing `frontend/src/pages/GoalsPage.test.tsx` plus `frontend/src/pages/goals/GoalsPage.regression.test.tsx` | Existing `goalInput.test.ts` covers extracted parsing/validation | Route regression covers the create form while existing table regression covers canonical Goal presentation | Native required inputs, textarea and submit button semantics remain intact | `GoalInventoryState` covers loading, backend error and empty inventory rendering | `GoalClient` remains the feature boundary and generic transport remains centralized |

## State ownership findings

The #985 decomposition continues to respect the platform ownership model:

- canonical Agent, Automation, Template, Organization and Goal lifecycle/domain state is fetched from or mutated through Control Plane domain clients;
- feature-local state is limited to presentation and interaction concerns such as form drafts, selected collaboration context, pagination state, busy flags and local validation messages;
- no new frontend shadow lifecycle state was added by #1160;
- no page or extracted component introduces direct ad-hoc HTTP calls;
- the repository transport-boundary check remains authoritative for the centralized `ApiTransport` invariant.

## Route and compatibility findings

#1160 does not change route paths, navigation contracts or compatibility re-exports. The production changes only name small render-state boundaries (`AutomationInventoryState`, `TemplateLibraryState`, `GoalInventoryState`, `OrganizationsFeedback`, `PersonalOrganizationScope`) so the existing loading/error/empty behavior can be regression-tested deterministically.

## Accessibility and interaction findings

The decomposition continues to rely primarily on native HTML interaction semantics. Regression coverage now explicitly checks representative native buttons, disabled states, form labels/accessible names and route forms. No new custom keyboard-navigation layer or focus-management abstraction was introduced by #985, so #1160 does not add browser E2E tests solely to duplicate native button and form behavior.

## Validation required before closure

The issue is complete only when the branch passes:

- frontend contract generation check;
- frontend transport-boundary check;
- frontend typecheck;
- frontend Vitest suite;
- frontend production build;
- all required repository CI and branch-protection checks.
