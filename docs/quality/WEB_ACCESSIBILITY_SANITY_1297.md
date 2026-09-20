# Web accessibility and narrow-viewport release sanity (#1297)

This document defines the retained practical Web sanity evidence consumed by the final #747
Audit Area 12 review. It is deliberately narrower than a formal accessibility conformance audit
and **does not claim WCAG certification**.

## Retained acceptance path

The maintained real-browser owner is:

- `frontend/tests/browserFirstRun.browser.mjs`

The check extends the existing #1164/#1234 browser-first acceptance architecture instead of
introducing a second Web lifecycle or test stack. It uses real headless Chromium against the
running single-node Control Plane and current Web client.

The exact commit/release candidate is the evidence boundary. A later material Web or shell change
requires the affected sanity checks to be rerun.

## Viewports

The retained smoke uses both:

- narrow/mobile: **390 x 844 CSS px**;
- desktop: **1280 x 900 CSS px**.

The first-user entry is exercised at the narrow viewport before the normal first-run workflow
continues. The completed authenticated shell is then checked at both desktop and narrow widths.

## Keyboard and responsive evidence

The real-browser path retains evidence for:

| Area | Retained check |
| --- | --- |
| First-user/auth entry | labelled Username/Password/Confirm password fields, sequential Tab traversal, Shift+Tab reversal, visible keyboard focus and readable mismatch validation |
| Visible focus | representative links, form fields and buttons must expose a non-zero visible focus outline when reached by keyboard |
| Desktop shell | Skip-to-content and primary Home/Chat navigation are traversed in DOM order with Tab/Shift+Tab |
| Mobile shell | keyboard reaches the menu toggle, Enter opens it, Shift+Tab reaches navigation, Enter navigates, and the route change closes the menu |
| Narrow layout | document-level horizontal overflow is rejected on representative primary screens |
| Search | the real Search surface remains reachable and its query control remains usable at 390 CSS px |
| Operator diagnostics | the completed first-run Task is reopened in Observability and its timeline table remains contained by the intentional scroll wrapper |
| Dense tables | the representative table wrapper must expose `overflow-x: auto|scroll`; real overflow, when present, must be horizontally scrollable without forcing document-level overflow |
| Primary Task action | a canonical draft Task is opened at the narrow viewport and its Queue action is reached by sequential Tab navigation with visible focus |
| Destructive confirmation | Tab from Queue reaches Cancel; Enter on the keyboard-focused Cancel action must open the native confirmation, and dismissing it must leave canonical Task state unchanged |
| Canonical navigation | the maintained first-run test continues to retain exact Task/Run/Result/Artifact links and public-Control-Plane identity checks |
| Search/diagnostics | the existing #1296 Search -> Observability -> readiness path remains part of the same maintained browser run |

## Release evidence

For a release candidate to consume this sanity pass:

1. the real Chromium browser-first check must pass on that exact head;
2. the frontend contract check, TypeScript check/tests and production build must remain green;
3. any defect discovered by the sanity pass must be fixed and the affected checks rerun;
4. the final #747 readiness report must reference the exact audited commit/run rather than treating
   this document alone as proof.

CI artifacts from a failed browser-first run retain the screenshot, page text and backend/Vite
process log already emitted by the maintained harness.

## Scope boundary

This is a practical release-readiness sanity check. It intentionally does not replace a dedicated
accessibility scanner, manual assistive-technology review or formal WCAG audit if those are added
later.
