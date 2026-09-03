# ADR 0006: Keep the web-client framework noncanonical

- **Status:** accepted
- **Date:** 2026-09-03
- **Issue:** #17

## Context

The platform needs a maintainable web application, but its browser framework must not become another source of platform lifecycle, identity or backend coupling. The canonical northbound boundary already exists at `/api/v1` through issue #32.

## Decision

Use React + TypeScript with Vite for the initial #17 web client, while declaring the framework **noncanonical**.

Canonical ownership remains in platform contracts and the versioned Control Plane. Frontend route keys for domain resources use canonical platform IDs. The browser may not import backend/provider types or call provider-private services directly.

The Control Plane client, error mapping and live-event client are isolated under `frontend/src/api/` so a future rendering-framework replacement does not require Task/Run/Event schema or backend lifecycle migration.

## Consequences

Positive:

- a mature component model for the large long-term UI surface;
- strict TypeScript checks around the Control Plane contract;
- small initial runtime dependency set;
- Vite provides development and static production builds without adding a server-side framework requirement;
- UI implementation remains replaceable because platform semantics stay outside React.

Constraints:

- deployment must route browser `/api` traffic to the Control Plane and serve `index.html` for client-side routes;
- native EventSource authentication is cookie/credential-oriented unless the live client is later replaced with a fetch-stream implementation;
- React state is presentation state only and must not become lifecycle truth.

## Alternatives considered

- **Framework-free DOM client:** fewer dependencies, but significantly higher maintenance cost for the intended multi-surface application.
- **Full-stack React framework:** rejected for the baseline because server-rendering/backend conventions would introduce unnecessary deployment and ownership assumptions.
- **Direct backend-specific dashboards:** rejected because they violate the API-first architecture and replacement boundaries.

## Replacement rule

React/Vite may be replaced independently when there is a clear UX, maintenance or deployment benefit. Such a replacement must continue to consume the same versioned Control Plane and canonical identities.
