# Web UI

The Web UI is a replaceable northbound client of the versioned Control Plane. It reads and mutates
canonical product state only through the public `/api/v1` boundary; it does not connect directly to
orchestrators, model gateways, MCP servers, Workers, storage databases, queues, connector backends,
or other provider-private services.

## Product surface

The maintained browser shell covers the supported product domains exposed by the active Control
Plane manifest, including:

- onboarding, authentication/session flow, Home and diagnostic status;
- Projects and Workspaces, Repositories, Tasks, Goals, Runs, Plans, Steps, Results, Files and Artifacts;
- Agents, Agent Teams, Verification and Approvals;
- Memory, Knowledge, Search, Research Evidence and Import / Export;
- Tools/Capabilities, Integrations/Connectors, Models/Providers, Evaluations and Templates;
- Compute Nodes/Workers/jobs, Applications, Terminal, Automations, Plugins and Marketplace;
- Governance, Decision Records, Organizations, Notifications, Events, Observability, Usage and Settings.

Optional or advanced domains are manifest-gated and show an unavailable/degraded state when the
active deployment does not advertise the required resources or commands. Governed Learning is
explicitly Experimental; repository-wide role and stability definitions live in
[FEATURE_CLASSIFICATION.md](../docs/FEATURE_CLASSIFICATION.md).

The browser does not create a second lifecycle or authorization authority. Task/Run state,
permissions, Approval decisions, Verification state, provider health and other canonical resources
remain server-owned.

## Run locally

Prerequisites: Node.js 22.22.2+ and npm 11.6.x. npm 10.9.8 has a known Arborist `edgesOut`
resolver crash with modern dependency graphs, so the frontend pins npm 11.6.0 in `packageManager`
and CI.

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server is `http://127.0.0.1:5173` and proxies `/api` to the local Control Plane on
`http://127.0.0.1:8000`. For another deployment, copy `.env.example` and set
`VITE_CONTROL_PLANE_URL` to the browser-visible Control Plane origin.

For the supported production-shaped same-origin composition, use the deployment guidance in
[DEPLOYMENT.md](../docs/operations/DEPLOYMENT.md).

## Quality gates

```bash
npm run typecheck
npm test
npm run build
```

The production build emits Vite's generated bundled-license inventory under
`dist/.vite/license.md`.

See [FRONTEND.md](../docs/FRONTEND.md) for the client architecture/security boundary and
[MULTI_AGENT_FIRST_RUN.md](../docs/product/MULTI_AGENT_FIRST_RUN.md) for the maintained browser-first
reference workflow.
