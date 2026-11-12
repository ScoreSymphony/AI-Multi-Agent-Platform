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
- deterministic normal/wide dashboard widget sizing;
- a persisted personal start/default page;
- live preview before save;
- reset to built-in defaults;
- versioned JSON import/export with v1-to-v2 migration.

The browser shell consumes semantic configuration rather than treating appearance as authorization or
feature state. Hiding a navigation entry never disables the route, capability or Control Plane
resource.

Home and Settings are intentionally non-hideable recovery routes. A collapsed desktop navigation
retains a persistent Menu control.

## Schema, validation and migration

The frontend schema lives in `frontend/src/customization/model.ts` and currently has version `2`.
Version `1` remains an accepted migration input. Unsupported versions fail closed instead of being
silently reinterpreted.

The v1-to-v2 migration adds:

- `layout.startPage`, defaulting to `/`;
- `dashboard.widgetSizes`, with status metrics defaulting to `wide` and recent Task/Run widgets to
  `normal`.

Migration is implemented on both sides of the persistence boundary. The browser migrates imported
JSON and the legacy `ai-multi-agent-platform.frontend-customization.v1` cache into the v2 cache. The
Control Plane accepts v1 payloads for migration, and the SQLite repository migrates previously stored
v1 rows when they are read. New writes are emitted as the current schema.

Persisted values are normalized before use. Colors are restricted to six-digit hexadecimal values,
numeric ranges are bounded, navigation paths are validated and only supported dashboard widget IDs
and sizes are accepted. Start-page values must be local path-only routes without query strings,
fragments or protocol-relative forms.

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
- `--dashboard-metrics-grid-column`
- `--dashboard-recent-tasks-grid-column`
- `--dashboard-recent-runs-grid-column`

New shared shell/components should consume semantic variables instead of introducing a second fixed
visual identity.

## Canonical personal persistence

Personal frontend customization is exposed through the canonical Control Plane as:

- resource: `frontend-preferences`;
- command: `frontend-preference.update`;
- command: `frontend-preference.reset`.

The backend implementation lives below the existing configuration authority at
`ai_multi_agent_platform.configuration.frontend_preferences`; frontend presentation preferences do
not create another root domain or lifecycle authority.

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

Personal frontend preferences are deliberately excluded from global search. They are private
presentation state bound to the authenticated principal, while generic search enumeration does not
carry that principal ownership context.

## Scope precedence

The schema defines deterministic least-specific-to-most-specific layer resolution:

1. platform defaults;
2. deployment / organization / workspace customization where applicable;
3. personal user customization.

The current durable Control Plane implementation owns the **personal** layer. Deployment,
organization and workspace layers are intentionally not stored in unrelated Organization/Workspace
metadata merely to complete the hierarchy. They require explicit owning contracts before becoming
durable.

A future inherited-scope implementation must persist sparse overrides (or an equivalent explicit
inheritance representation) rather than full duplicated normalized themes. Otherwise a personal
snapshot would unintentionally shadow every inherited deployment/organization/workspace value.

## Start-page semantics

`layout.startPage` is personal presentation state. The shell applies it at most once after
authentication and frontend-preference hydration, and only when the browser entered the authenticated
shell at `/`.

The redirect is restricted to a canonical navigation route. If that navigation entry depends on an
API resource, the current Control Plane manifest must advertise that resource. Deep links, setup
flows, unavailable routes and invalid values are therefore never overwritten by the start-page
preference. `/` remains the fallback.

## Dashboard and navigation semantics

Navigation customization affects discoverability/presentation only. Authorization and feature
availability remain server-authoritative.

Dashboard customization controls visibility, ordering and supported width for the canonical
overview's:

- status metrics;
- recent Tasks;
- recent Runs.

Each widget supports `normal` or `wide` sizing. The model is intentionally deterministic and does not
encode arbitrary CSS, absolute coordinates or executable layout data. It can later be consumed by a
richer drag-and-drop editor without changing the persisted ownership boundary.

The data sources themselves remain unchanged canonical Control Plane reads.

## Import, export and recovery

Export emits the normalized current schema. Import accepts the current schema and supported legacy
versions, migrates them, and only applies the result as preview state until the user explicitly saves.

Reset removes both current and legacy browser caches and resets the canonical personal preference
when the Control Plane resource is available. Home and Settings remain visible regardless of
navigation customization, preserving a route back to recovery controls.

## Remaining #1487 work

The following acceptance areas remain beyond the current implementation:

- reusable named themes with create/save/edit/rename/duplicate/switch lifecycle;
- explicit deployment/organization/workspace preference ownership, sparse inheritance and server-side
  layer composition;
- optional top-navigation shell if it can preserve responsive and accessibility guarantees;
- richer drag-and-drop/arbitrary dashboard placement beyond deterministic visibility/order and
  normal/wide sizing;
- broader replacement of feature-local hard-coded presentation values;
- end-to-end accessibility and contrast validation across arbitrary supported themes.
