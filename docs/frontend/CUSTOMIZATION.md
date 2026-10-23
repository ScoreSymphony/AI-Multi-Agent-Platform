# Frontend customization

Issue #1487 defines a versioned presentation-configuration boundary for the browser frontend.

## Current capabilities

The current implementation supports:

- semantic color tokens for background, surfaces, text, accents and status colors;
- system-sans, serif and monospace font presets plus font scaling;
- compact, comfortable and spacious density;
- configurable control/card radius and card-shadow presets;
- content-width control;
- application name, navigation subtitle and a bounded custom raster logo/favicon;
- left/right navigation placement, configurable width and an optional collapsed startup state;
- navigation visibility and ordering;
- configurable overview widgets for status metrics, recent Tasks and recent Runs;
- live preview before save;
- reset to built-in defaults;
- versioned JSON import/export.

The browser shell consumes semantic configuration rather than treating appearance as authorization or
feature state. Hiding a navigation entry never disables the route, capability or Control Plane
resource.

Home and Settings are intentionally non-hideable recovery routes. A collapsed desktop navigation
retains a persistent Menu control.

## Schema and validation

The frontend schema lives in `frontend/src/customization/model.ts` and currently has version `1`.
Imports with an unsupported version fail closed instead of being silently reinterpreted.

Persisted values are normalized before use. Colors are restricted to six-digit hexadecimal values,
numeric ranges are bounded, navigation paths are validated and only supported dashboard widget IDs
are accepted.

Custom branding images are limited to PNG, JPEG or WebP data URLs and 256 KiB at upload time. Remote
image URLs and SVG are deliberately rejected so theming does not become an untrusted remote-content
or active-markup injection boundary.

## Design tokens

The customization model maps into semantic CSS custom properties such as:

- `--page-background`
- `--page-highlight`
- `--surface`, `--surface-2`, `--surface-raised`
- `--sidebar`
- `--text`, `--muted`
- `--accent`, `--accent-strong`
- `--line`, `--line-soft`
- `--danger`, `--warning`, `--success`
- `--font-family`, `--font-scale`
- `--density-scale`
- `--control-radius`, `--card-radius`, `--card-shadow`
- `--sidebar-width`
- `--content-max-width`

New shared shell/components should consume semantic variables instead of introducing a second fixed
visual identity.

## Canonical personal persistence

Personal frontend customization is exposed through the canonical Control Plane as:

- resource: `frontend-preferences`;
- command: `frontend-preference.update`;
- command: `frontend-preference.reset`.

The server derives the owning principal from the authenticated `RequestContext`. The browser cannot
supply a different owner identity. Each mutation carries an expected revision so stale tabs fail with
a canonical conflict instead of silently overwriting a newer preference.

The single-node implementation stores the personal record in
`db/frontend-preferences.sqlite3`. The store is durable and participates in backup/restore when
present, but it is not a platform-readiness dependency: losing a cosmetic preference must not make
Tasks, Runs or the Control Plane unavailable.

The browser retains a validated local copy as a startup cache/fallback. Once the canonical resource
contains a saved preference, that server record hydrates the shell. A temporary preference-read
failure leaves the safe local cache usable instead of turning presentation state into product
availability authority.

## Scope precedence

The schema already defines deterministic least-specific-to-most-specific layer resolution:

1. platform defaults;
2. deployment / organization / workspace customization where applicable;
3. personal user customization.

The current durable Control Plane implementation owns the **personal** layer. Deployment,
organization and workspace layers are intentionally not stored in unrelated Organization/Workspace
metadata merely to complete the hierarchy. They require explicit owning contracts before becoming
durable.

## Dashboard and navigation semantics

Navigation customization affects discoverability/presentation only. Authorization and feature
availability remain server-authoritative.

Dashboard customization currently controls visibility and ordering for the canonical overview's:

- status metrics;
- recent Tasks;
- recent Runs.

The data sources themselves remain unchanged canonical Control Plane reads.

## Remaining #1487 work

The following acceptance areas remain beyond the current implementation:

- explicit deployment/organization/workspace preference ownership and server-side layer composition;
- optional top-navigation shell if it can preserve responsive and accessibility guarantees;
- persisted configurable start/default page;
- richer dashboard widget sizing/placement beyond deterministic visibility/order;
- migration hooks for future customization schema versions;
- broader replacement of feature-local hard-coded presentation values;
- end-to-end accessibility/contrast validation across arbitrary supported themes.
