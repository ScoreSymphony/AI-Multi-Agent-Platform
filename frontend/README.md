# Web UI

Issue #17 frontend for the AI Multi-Agent Platform.

The browser is a **northbound client of the canonical Control Plane only**. It must not connect directly to Hermes, Forge, model gateways, MCP servers, workers, storage databases, queues, or provider-private identifiers.

## Current vertical slice

- stable platform navigation shell for the ideal product areas;
- Home/Overview with health and recent Task/Run activity;
- Task list, create and detail;
- Task queue/start/cancel/retry commands;
- Run list and detail;
- canonical Plan/Step/Artifact/Result references;
- Task timeline including derived observability entries;
- SSE `platform.event` live updates with reconnect state;
- canonical API error presentation;
- explicit unavailable/degraded pages for later subsystems;
- responsive and keyboard-accessible shell baseline.

## Run locally

Prerequisites: Node.js 22.12+ and a Control Plane available on `http://127.0.0.1:8000`.

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server is `http://127.0.0.1:5173` and proxies `/api` to the local Control Plane. For another deployment, copy `.env.example` and set `VITE_CONTROL_PLANE_URL` to the browser-visible Control Plane origin.

## Quality gates

```bash
npm run typecheck
npm test
npm run build
```

The production build emits Vite's generated bundled-license inventory under `dist/.vite/license.md`.

See `docs/FRONTEND.md` for architecture and security boundaries.
