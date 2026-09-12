# Frontend API client boundary

`ApiTransport` in `transport.ts` is the only general-purpose HTTP transport for browser calls to the versioned Control Plane.

Domain clients under this directory own endpoint selection, typed request/response models, query encoding and narrowly scoped domain adaptations. They must delegate generic request behavior to `ApiTransport` instead of defining their own JSON parser, generic error mapper, timeout/retry loop, correlation-header construction or direct `fetch` request pipeline.

`BrowserSessionClient` is the approved browser-session boundary. It owns storage and rotation of the non-secret CSRF token and exposes a configured `transport` whose CSRF callback re-reads the current token before unsafe cookie-authenticated requests. The HttpOnly session credential remains inaccessible to frontend code. Its `fetch` member exists only as a compatibility bridge while older domain clients are migrated; new clients should accept/share `ApiTransport` directly.

## Transport policy

- Browser requests target the canonical `/api/v1` Control Plane only.
- `Accept`, JSON content headers and correlation IDs are constructed centrally.
- Canonical API error envelopes are preserved; malformed/non-JSON failures are normalized centrally with request/correlation diagnostics.
- Cookie-authenticated unsafe methods receive the current CSRF token; explicit Bearer requests do not.
- Timeouts and caller cancellation are normalized as transport errors.
- GET/HEAD reads may use the bounded transient retry policy. Mutations are never retried automatically merely because they carry an idempotency key.
- A caller that needs different retry semantics must make that policy explicit at the transport boundary; domain clients must not implement ad-hoc retry loops.
- Provider-, Hermes-, Forge-, MCP-, Worker-, database- and storage-private transports are not valid browser fallbacks.

## Domain client pattern

Prefer a constructor that can receive a shared transport while retaining the narrow compatibility options needed during migration:

```ts
export interface ExampleClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class ExampleClient {
  private readonly transport: ApiTransport;

  constructor(options: ExampleClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
  }

  get(id: string): Promise<Example> {
    return this.transport.request<Example>(`/examples/${encodeURIComponent(id)}`);
  }
}
```

The shell should share the `BrowserSessionClient.transport` instance across migrated clients so session/CSRF, timeout, diagnostics and retry behavior stay coherent.

`npm test` runs `scripts/check-transport-boundary.mjs` before Vitest. The guard contains a temporary inventory of legacy domain clients that still own transport helpers. Remove entries as they migrate; do not add new entries as a shortcut for new transport implementations.
