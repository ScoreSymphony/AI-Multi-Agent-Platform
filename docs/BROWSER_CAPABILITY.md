# Browser and Web Capability

Issue #74 adds a platform-owned browser/web boundary. Browser engines remain replaceable
providers behind the existing capability registry and invocation pipeline.

## Canonical boundary

`BrowserProvider` extends the canonical capability tool-provider seam and publishes normalized
`BrowserProviderFeatures`. The platform-facing contract does not expose Playwright, CDP,
Selenium, Chromium profile IDs, cookies, or remote-browser session IDs.

`BrowserSessionRef` is the canonical reusable session reference. It can carry owner, project,
task, run and agent scope, timestamps, an optional opaque storage-profile reference, privacy
classification, allowed-domain metadata and namespaced adapter metadata. Raw cookies and
credentials are provider-private state.

The reference provider registers these canonical capabilities:

- `browser.navigate`
- `browser.extract`
- `browser.follow_link`
- `browser.submit_form`
- `browser.download`
- `browser.close_session`

JavaScript execution and screenshots are deliberately not required by the contract. Providers
advertise them as feature metadata. The standard-library reference provider reports both as
unsupported so a richer Playwright/CDP/remote adapter can add them without changing task or
agent contracts.

## Reference implementation

`StdlibBrowserProvider` is a small self-hosted HTTP/HTML adapter built with the Python standard
library. It exists to prove the boundary without adding a mandatory browser-engine dependency.
It supports isolated cookies, navigation, text/link extraction, link following, HTML form
submission, one canonical file upload per form submission, downloads into `FileProvider`, and
session reuse/closure.

It is not intended to emulate a modern JavaScript browser. A production deployment that needs
JavaScript, screenshots, DOM interaction or interactive browser control should register another
`BrowserProvider` with the same canonical capability IDs and appropriate feature metadata.

## Provider binding, placement and replacement

`BoundBrowserProvider` is a provider-neutral decorator that can wrap the stdlib adapter or any
future browser implementation. `BrowserPlacement` can attach canonical registration-level
`node_id`, `worker_id` and priority plus provider-private worker labels without modifying the
canonical `CapabilitySpec` or browser task request.

Placement stays on the registration boundary deliberately. Provider-specific worker labels must
not be written into the shared capability definition, because multiple replaceable providers for
the same capability/version must continue to publish an identical canonical capability contract.
A later #14 scheduler can use node/worker registration data and browser feature metadata to place
work without changing task logic.

## Security and trust

Every outbound request in the reference adapter passes through `BrowserNetworkPolicyHook`.
`DefaultBrowserNetworkPolicyHook` supports HTTP/HTTPS restrictions, domain allow/deny lists,
blocks URL-embedded credentials, blocks localhost by default, and resolves targets to reject
private, loopback, link-local, reserved, unspecified and multicast addresses unless private
network access is explicitly enabled. Redirect targets are checked again.

Production deployments should additionally enforce network controls at the sandbox/container or
worker boundary. DNS pinning/rebinding hardening and infrastructure-level egress controls belong
to the production security layer; the canonical hook remains replaceable.

Page text/HTML returned by the reference adapter is marked `untrusted_web_content`. Agents and
orchestrators must treat page instructions as untrusted external input, not as platform policy or
system instructions.

`browser.submit_form` is classified as a restricted external side effect so the existing #15
authorization/approval bridge can deny or gate it. Downloads are restricted local writes and
require browser-read plus file-create permissions. File access itself still goes through the
injected `FileProvider`; production wiring must use the authorization-enforced provider wrapper
where policy requires it.

## Files, downloads and uploads

Uploads accept only canonical `file_*` references and read through `FileProvider`; host paths and
temporary browser paths never become canonical input. Downloads are assigned a new canonical
file ID and written through `FileProvider` with source URL, content type, SHA-256 checksum,
download timestamp, provenance and untrusted-content classification. The canonical tool result
returns the file reference as result/evidence.

`DownloadValidationHook` is evaluated before persistence. The baseline blocks a small executable
MIME set and is intentionally replaceable by malware scanning or organization policy.

## Sessions and isolation

The reference provider keeps cookies in a private cookie jar per canonical session. Session reuse
requires the same owner and project context. A provider cannot expose its native browser/session
identifier through the canonical session object.

The session model can additionally bind task/run/agent IDs. The current generic `ToolInvocation`
provider seam carries operation ownership/project context while task/run/agent traceability is
retained by `CapabilityInvoker`/invocation observers. Richer browser services can construct more
specific session scopes without changing the canonical session type.

## Evidence and observability

`BoundBrowserProvider` emits a namespaced `browser.operation` metadata record for successful
operations and canonical `ContractError` failures. It contains the canonical browser capability
ID/operation, outcome, duration, requested/final domain, redacted requested/final target,
content-trust classification, produced result reference where present and canonical error code.

Browser target metadata is redacted before emission: credentials, query strings and fragments are
removed so ordinary traces do not accidentally persist URL tokens or other high-risk parameters.
Deployments may still apply stricter redaction through their observability stack.

The canonical `CapabilityInvoker` carries provider result metadata into successful
`InvocationRecord` objects and provider `ContractError.adapter_metadata` into failed invocation
records. Browser traces therefore retain task/run/agent/project/correlation IDs, provider,
node/worker placement, status/error category and redacted browser operation metadata in one
canonical observability path instead of a browser-specific parallel audit system.

Produced downloads remain referenced through `result_ref`/`evidence_refs`; future screenshot or
snapshot-capable adapters can use the same evidence-reference seam without changing the browser
request contract.

## Worker and execution placement

Browser task schemas do not encode local-process assumptions. Browser providers register through
`CapabilityRegistry`, whose registrations already carry provider/node/worker placement metadata.
`BoundBrowserProvider` exposes this placement explicitly for browser adapters while preserving the
same capability ID and request shape. A later local sandbox, dedicated browser worker, remote
browser service or #14 scheduler integration can therefore implement the same capability IDs.
The #7 execution boundary remains the place for sandbox/execution implementation details rather
than browser-domain contracts.

## Failure semantics

The reference provider maps network failures into canonical `ContractError` categories. Timeouts
and cancellation also flow through the existing `CapabilityInvoker` pipeline, preserving task,
run, agent, project and correlation trace records. Unsupported capabilities fail through the
registry instead of silently falling back to provider-specific behavior.

When the provider binding observes a canonical browser failure it appends redacted operation
metadata to the existing `ContractError`; the canonical error code and retry semantics remain
unchanged.

## Example bootstrap

```python
files = LocalFileProvider("./data/files", "./data/files.sqlite")
reference = StdlibBrowserProvider(
    files,
    network_policy=BrowserNetworkPolicy(
        allowed_domains=("example.org",),
        allow_private_networks=False,
    ),
)
browser = BoundBrowserProvider(
    reference,
    placement=BrowserPlacement(
        node_id="node_browser_1",
        worker_id="worker_browser_1",
        worker_labels=("browser-runtime", "sandboxed"),
    ),
)
registry = CapabilityRegistry()
await registry.register_provider(browser)
```

The same canonical browser requests still work if the bound provider is later replaced by a
Playwright/CDP/remote-browser adapter. A deployment that disables browser access simply does not
register a browser provider; callers then receive the normal canonical unsupported-capability
error.
