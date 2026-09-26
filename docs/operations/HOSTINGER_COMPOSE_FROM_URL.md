# Hostinger Compose from URL

Status: repository candidate, not release-approved. Parent acceptance issues: #1446 and #1466.

The primary installation contract is Docker Manager → Compose → Compose from URL, one raw URL,
a freely chosen project name, and Deploy. Catalog/One Click is not a substitute for this contract.
The sole new-install candidate is `deploy/docker/docker-compose.hostinger-compose-from-url.yml`:

```text
https://raw.githubusercontent.com/ScoreSymphony/AI-Multi-Agent-Platform/main/deploy/docker/docker-compose.hostinger-compose-from-url.yml
```

That main URL becomes available only after merge. Test the PR branch URL before merge, with its
build source context pinned to the recorded gateway implementation revision. Never test a branch Compose file that
silently builds an older gateway from main. No environment entry or second deployment is part of the
acceptance test. This candidate does not yet guarantee hPanel Open.

## Runtime ingress

Compose supplies its standard `COMPOSE_PROJECT_NAME`; no provider-specific interpolation is used.
There is no fixed top-level `name` overriding hPanel's project selection. Choose a DNS-compatible
project label such as `agents`, `test-platform`, or `ai-multi-agent-platform` (1–63 characters,
letters/digits/hyphens, no leading/trailing hyphen). Compose also permits names such as `my_project`
that cannot be DNS labels; these must fail closed rather than produce invalid certificates.

The gateway joins only the host UTS namespace (`uts: host`). The existing entrypoint accepts
`srv<digits>` and `srv<digits>.hstgr.cloud`, normalizes the former, validates the resulting DNS
name, and derives `<project>.srv<digits>.hstgr.cloud`. Invalid runtime identities remain
setup-pending and fail the candidate's gateway readiness check. There is no external discovery.

Static, anchored project-scoped HostRegexp/HostSNIRegexp rules route the Hostinger namespace;
they are not catch-all rules. They necessarily accept that project's numeric Hostinger namespace
at the edge because the actual VPS ID is unavailable at Compose time. Caddy only serves the
single derived runtime domain. This assumes the observed Traefik v3 Docker provider on host
networking can reach the private bridge. Other Traefik versions/topologies require provider
validation; the candidate is not claimed to work on every arbitrary VPS topology.

Hostinger Traefik owns public 80/443. HTTP reaches Caddy for HTTPS redirect and ACME challenge;
TLS passes through to Caddy, which obtains and persists the certificate and proxies to private
Web, then the canonical Control Plane API. Managed DNS resolution and public ACME reachability
remain provider prerequisites, to be checked in the smoke. No DNS setup is requested from users.

Exactly three services start: `control-plane`, `web`, `hostinger-gateway`. No host ports are
published by this Compose file. Internal service ports are 8000, 8080, and 80/443; gateway readiness
uses internal 8081. Secure cookies stay enabled. Named volumes retain platform data and gateway
TLS state. All services have healthchecks, restart policies and no-new-privileges; capabilities
are dropped, with only NET_BIND_SERVICE granted to Caddy. The gateway filesystem is read-only.
No Docker socket, privileged container, host filesystem mount or Hostinger credential is used.

## hPanel Open: measured blocker

The repository cannot currently promise an automatic Open target. Runtime hostname discovery
cannot modify container labels, Compose interpolation or hPanel metadata. No documented
Compose label for a runtime-generated Open URL was found in the reviewed provider documentation.
That is a documentation finding, not proof that all undisclosed provider mechanisms are impossible.

Evidence from real tests, preserved in [#1446](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/issues/1446),
[#1466](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/issues/1466), and
[closed PR #1471](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/pull/1471):

- URL-import probes rendered `HOSTNAME` and `TRAEFIK_HOST` as missing.
- The supplied provider test record also reports VPS_HOSTNAME, HOSTINGER_HOSTNAME,
  HOSTINGER_VPS_HOSTNAME, VPS_IP, PUBLIC_IP, VPS_ID and PUBLIC_PORT unavailable.
- No published port plus regex routes yielded Running containers but only Terminal in hPanel.
- Publishing 8080 yielded Open → `http://<VPS-IP>:8080`, but the public path failed on that VPS.
- Exact Host/HostSNI routing requires a concrete hostname at Compose time in the existing fallback.

Consequently neither the rejected 8080 bootstrap nor a fake setup.invalid route is included.
Binding host 80/443 would conflict with the provider Traefik. Exposing a loopback-only port would
not make the browser's Open URL reachable. A redirect helps only after its entrypoint is reachable.

The smallest provider change would be injecting a validated VPS FQDN into generic Compose-from-URL
interpolation and deriving Open from the resulting exact Host rule. Alternatively, hPanel could
resolve the project's scoped HostRegexp using its known VPS identity and expose the concrete HTTPS
URL, without changing this candidate's runtime discovery. A documented runtime URL metadata
contract could also solve discovery. No private hPanel endpoint, socket mutation or API key is
acceptable as a repository workaround.

Reviewed public sources on 2026-09-26:

- [Deploy your first container](https://www.hostinger.com/support/12040815-how-to-deploy-your-first-container-with-hostinger-docker-manager/): generic URL/project import and direct IP/port access.
- [Connect Compose projects with Traefik](https://www.hostinger.com/support/connecting-multiple-docker-compose-projects-using-traefik-in-hostinger-docker-manager/): exact Host rule and explicit TRAEFIK_HOST contract.
- [Change a Docker project's domain](https://www.hostinger.com/support/how-to-change-the-domain-of-a-docker-project/): temporary hostnames for catalog apps, not a generic runtime Open label contract.

## Real-provider smoke and merge gate

1. Record candidate commit, raw branch URL and VPS Docker/Traefik versions. Use a fresh project
   name, for example `test-platform`; do not replace existing projects or volumes.
2. Docker Manager → Compose → Compose from URL. Paste the candidate raw URL, enter the project
   name, and click Deploy once. Enter no environment variables, DNS or firewall changes.
3. Wait for exactly the three platform containers to be Running/Healthy. Record build logs and
   each health result. Confirm there are no diagnostic containers or published application ports.
4. Inspect Access. Record whether Open exists and its complete URL. If absent, mark Open FAIL;
   do not insert TRAEFIK_HOST or redeploy to turn the fallback into an acceptance pass.
5. If Open exists, click it from a public browser. Require the correct project HTTPS URL,
   trusted certificate and loaded application. Do not bypass a certificate warning.
6. Independently test the derived HTTPS URL for ingress diagnosis if Open is missing; a manually
   entered working URL is ingress evidence only, not an Open acceptance pass.
7. Repeat with another project name only after the first run is recorded. Retain logs/screenshots
   and the exact revision. No user metadata should be committed into deployment configuration.

Do not merge unless this smoke passes, relevant CI is green and finished, review threads are
resolved, and the branch is zero commits behind main. Update with rebase, never a merge from main.
Keep #1446/#1466 open while automatic Open is unproven or failing.

## Compatibility

Existing `docker-compose.hostinger.yml` and `docker-compose.hostinger-https.yml` are historical
shared-edge profiles and retain their behavior. `docker-compose.hostinger-zero-config.yml` retains
explicit-domain compatibility. `docker-compose.hostinger-managed.yml` remains the manual
TRAEFIK_HOST operator fallback. None is the new-install zero-input standard. Catalog packaging
remains separate optional work and does not replace Compose-from-URL acceptance.

## Additional real-provider result, 2026-09-26

The candidate at Compose revision `f3b621dca85571f3b1f497a8cfa6664c720ab3cc` was imported through
exactly Compose from URL and deployed once as `test-platform`. Runtime builds used pinned source
`1b117c8bee2da5ce08827b382c64de3343e66734`. No environment, DNS or firewall edits were made.
All three containers were Running, the gateway logged the expected derived project hostname,
and Control Plane readiness returned 200. hPanel Access still offered only Terminal, not Open.
Running alone does not prove all three Docker health states or end-to-end HTTPS readiness.

Direct public HTTPS navigation returned `ERR_SSL_PROTOCOL_ERROR`. Caddy's certificate logs showed
HTTP-01 authorization failing with HTTP 404 and TLS-ALPN-01 validation failing with a connection
timeout. DNS comparison found identical A/AAAA answers for the project name, working Open reference
and base VPS name. This rules out a missing project DNS record as the observed explanation.
The precise certificate-path root cause remains unproved; no shared provider config was changed.

The inspected provider Traefik uses host networking, Docker discovery, web/websecure 80/443 and
its own HTTP-01 ACME resolver. [Traefik's entrypoint documentation](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/#allowacmebypass)
describes internal challenge routing and the static `allowACMEByPass` option for custom challenge
handlers. That setting belongs to the provider's edge; app-container labels cannot set it. The
observed 404 is consistent with interception, but is not proof by itself. ALPN timeout also needs
independent reachability diagnosis. A provider-supported certificate/challenge path is an additional
prerequisite alongside Open discovery. Neither arbitrary host-port publishing nor manual provider
Traefik changes is added to the normal installation contract.

## Working Hermes reference comparison

At the user's request, `hermes-agent-ur8x` was inspected read-only through hPanel on the same VPS.
Its Open action leads to the generated project HTTPS hostname; a public browser loaded the Hermes
sign-in page with no certificate warning. No credentials were entered and no reference settings
were saved or redeployed.

The reference Compose has one service, image `ghcr.io/hostinger/hvps-hermes-agent:latest`, and
`ports: ["4860"]`; Docker assigned a high public host port. Its relevant labels are the documented
provider pattern, not an additional hPanel URL extension:

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.${COMPOSE_PROJECT_NAME}.rule=Host(`${COMPOSE_PROJECT_NAME}.${TRAEFIK_HOST}`)
  - traefik.http.routers.${COMPOSE_PROJECT_NAME}.entrypoints=websecure
  - traefik.http.routers.${COMPOSE_PROJECT_NAME}.tls.certresolver=letsencrypt
  - traefik.http.services.${COMPOSE_PROJECT_NAME}.loadbalancer.server.port=4860
```

Its project environment already contains the actual VPS FQDN as `TRAEFIK_HOST`. Only that
non-secret hostname key was recorded; account keys/passwords were neither persisted nor published.
The inspected Compose defines no additional network, custom Open label, or top-level metadata
extension. There is no evidence that merely using its image or publishing its port causes hostname
injection for arbitrary Compose-from-URL projects.

This proves a working provider access/certificate path on the same VPS when an exact domain is
available to Traefik. It does not prove that the native template's injected environment is supplied
to generic URL imports; the previously recorded generic probes showed it absent. Nor does it prove
the reference's high mapped port is publicly reachable. We therefore do not copy that port, image,
VPS-specific hostname or catalog provisioning dependency into the platform's standard candidate.

The closest supported provider contract is automatic VPS-hostname injection for generic URL
imports plus the same exact Host rule and provider TLS resolver. It would address both hPanel URL
discovery and delegate certificate handling to the already working edge. Runtime UTS discovery
alone cannot retroactively supply that label. The reference comparison narrows the missing
mechanism; it does not convert the failed platform smoke into a pass.

### Official Hermes upstream review

The separately governed NousResearch upstream was inspected as reference-only material at
[`d0288be5b3330d2442e3907185b8e9d0958297bb`](https://github.com/NousResearch/hermes-agent/tree/d0288be5b3330d2442e3907185b8e9d0958297bb).
Its [Compose file](https://github.com/NousResearch/hermes-agent/blob/d0288be5b3330d2442e3907185b8e9d0958297bb/docker-compose.yml)
uses host networking with gateway/dashboard services and a loopback dashboard listener. Its
[Docker guide](https://github.com/NousResearch/hermes-agent/blob/d0288be5b3330d2442e3907185b8e9d0958297bb/website/docs/user-guide/docker.md)
describes ordinary container deployment and separately configured remote access. Neither supplies
Hostinger hostname injection, Traefik routing labels or hPanel Open metadata. The reviewed revision
has an MIT license; no upstream source was copied and the platform's approved Hermes adapter pin
and compatibility policy were not changed. The Hostinger image is a distinct distribution; its
source/provenance must not be inferred from the NousResearch repository name.

### Provider-side HTTP challenge evidence

A subsequent read-only inspection of the existing Traefik logs found four errors for the exact
smoke-test project between 12:17:56 and 12:24:29 UTC on 2026-09-26. The provider ACME handler
reported `Cannot retrieve the ACME challenge` for that project's domain. Challenge tokens and
public addresses are omitted. This directly establishes that the provider's own ACME handler
received the project's HTTP challenge requests and could not serve their tokens, consistent with
Caddy's corresponding HTTP-01 404 failures. It strengthens the diagnosis beyond the browser error;
it does not establish why the separate TLS-ALPN connection timed out.

A repeat deployment of the unchanged candidate cannot repair that handler ownership. The
[documented entrypoint option](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/#allowacmebypass)
allows application challenge handlers, but belongs to provider static configuration. It cannot be
set through the candidate's Docker labels. Changing that shared edge is neither part of the normal
installation nor sufficient evidence that automatic hPanel Open would work. Alternatively,
[Traefik's ACME resolver](https://doc.traefik.io/traefik/v3.5/reference/install-configuration/tls/certificate-resolvers/acme/#domain-definition)
needs concrete domains from `Host`/`HostSNI` or `tls.domains`; adding a resolver to a regex rule
cannot infer the runtime VPS hostname. Automatic hostname injection before Compose interpolation
remains the smallest documented-contract extension that would allow the working native exact-Host
pattern to be used by generic URL imports. No provider changes were made in this investigation.

### Bounded secure-router discovery experiment

The next candidate adds a project-named HTTP router on `websecure` with the native
`letsencrypt` resolver label and the same anchored project regex. Existing scoped TCP passthrough
remains the actual application TLS path. This tests whether hPanel recognizes the native secure
HTTP metadata independently of a literal Host rule; no provider documentation promises that
heuristic. Regex domains do not instruct Traefik to issue a concrete certificate. The added HTTP
router is not a trusted-certificate fallback if the TCP route fails. Acceptance still requires
observing both the generated Open URL and a verified application certificate on the real provider.

Read-only inspection also found that this test VPS has a custom active firewall accepting TCP443
only from one user source address, while TCP80 accepts all sources. Public TLS-ALPN validation
cannot use that restricted rule. The user was asked separately about an explicit test-environment
exception; no firewall change is assumed or included in the Compose installation contract.
