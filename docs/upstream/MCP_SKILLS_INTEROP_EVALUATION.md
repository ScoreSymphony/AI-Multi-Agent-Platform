# MCP Skills / SEP-2640 interoperability evaluation

Status: issue #966 proof of concept; **not a stable platform interface**.

## Decision

Recommendation: **`wait_for_ratification`**.

The architecture mapping is sound and the pinned v1 wire shape can be staged safely behind a
replaceable adapter, but SEP-2640 remains experimental and the wire capability does not advertise an
extension draft commit/version. A generic server can therefore declare
`io.modelcontextprotocol/skills` without proving that it implements the exact draft revision tested
here. The platform must not turn a shape-compatible declaration into a stronger compatibility claim.

The PoC is retained as revision-pinned evidence and as a reusable adapter seam. It must not be wired
as generally supported MCP Skills functionality until ratification, or until a future protocol shape
provides an equally strong revision/conformance witness.

## Exact evaluation pins

Evaluation date: 2026-09-13.

| Surface | Exact pin |
| --- | --- |
| SEP-2640 branch | `modelcontextprotocol/modelcontextprotocol@582d814a2fb1607aa46045d0cc7f14e0f4da9945` |
| Skills extension/reference docs | `modelcontextprotocol/ext-skills@d866efdba298b55b8156c7b7aa1bdebc1b625f4c` |
| MCP base protocol | `2026-07-28` |
| Extension ID | `io.modelcontextprotocol/skills` |
| Required methods in evaluated shape | `skills/list`, `skills/get` |
| File retrieval | standard `resources/read` |
| Optional method | `resources/directory/read` when `directoryRead: true` |
| Result envelope | `resultType="complete"`, numeric non-negative finite `ttlMs`, `cacheScope="public"|"private"` |
| Content binding | per-file `sha256:<hex>` digest plus exact byte size |
| Minimum conforming host capacity | 512 resources and 16 MiB total bytes per Skill |
| Packed archives / `skill://index.json` | not part of evaluated v1 shape |

The September 10 `ext-skills` revision explicitly states the `2026-07-28` baseline and current
`skills/list`/`skills/get` cacheable shape. Older archive/index designs are not accepted by this PoC.

## Authority boundary

The only supported architecture direction is:

```text
MCP Skills discovery/fetch
        -> revision-pinned adapter
        -> bounded immutable staging
        -> canonical external-Skill candidate
        -> #588 provenance/security/trust lifecycle
        -> SkillRevision
        -> ordinary SkillBundle
```

The following direction is explicitly rejected:

```text
MCP server -> canonical Skill authority
```

`#588` remains authoritative for canonical Skill identity, immutable revisions, trust transitions,
resolution and bundles. `#12` remains authoritative for canonical capabilities. `#15` remains
authoritative for authorization/approval. MCP metadata is source evidence only.

## Wire-shape findings

The evaluated extension declares support under:

```json
{
  "capabilities": {
    "resources": {},
    "extensions": {
      "io.modelcontextprotocol/skills": {
        "directoryRead": false
      }
    }
  }
}
```

The current v1 shape requires `skills/list` and `skills/get`. Both use the pinned MCP
`CacheableResult` envelope:

- `resultType` must be `"complete"`;
- `ttlMs` must be a non-negative finite JSON number;
- `cacheScope` must be `"public"` or `"private"`.

Missing or malformed envelope fields fail explicitly. The PoC does not optimistically accept a
partial result shape.

Each `Skill` entry carries:

- `uri`: the server-scoped URI of `SKILL.md`;
- `frontmatter`: verbatim JSON representation of `SKILL.md` frontmatter;
- `resources`: either a complete per-file manifest or the string `"dynamic"`;
- each manifest resource: `uri`, `sha256:<hex>` digest and exact byte `size`.

For the pinned extension shape, the final Skill path segment must equal `frontmatter.name`. The PoC
validates that invariant for top-level and nested/custom-scheme Skill URIs before intake.

The pinned v1 extension also fixes host minimum support at 512 resource entries and 16 MiB total file
content per Skill. `McpSkillsLimits` defaults meet those minimums; deployments may deliberately apply
a different local policy only by supplying explicit limits and must not describe a tighter policy as
full v1 host conformance.

The extension treats `(host-assigned server identity, skill URI)` as remote identity. The platform
preserves both as source/provenance metadata but does not use either as canonical permission or trust
authority.

### Revision-negotiation gap

There is no extension-draft commit/version field in the evaluated wire capability. The handshake can
prove the MCP base protocol revision and the advertised settings, but it cannot prove that a remote
implementation corresponds to `582d814...` or `d866efd...`.

The PoC therefore:

1. pins the exact revisions used to define and test the parser;
2. fails closed on a different MCP base revision or unknown/malformed capability/result shape;
3. limits compatibility claims to the local/reference fixture that encodes the pinned shape.

It **does not** claim that an arbitrary server advertising the same extension ID is commit-identical.
This is the primary reason for `wait_for_ratification`.

## Discovery mapping

| MCP field | Canonical handling |
| --- | --- |
| server identity | provider/source provenance |
| Skill `uri` | provider/source metadata; never a canonical Skill ID by itself |
| `frontmatter.name` | candidate display/name evidence plus URI/name consistency check |
| `frontmatter.description` | candidate description evidence |
| `frontmatter.license` | source claim; `UNKNOWN` when absent and still requires verification |
| complete `resources` manifest | integrity/staging input |
| per-file digest and size | mandatory pre-intake verification |
| `allowed-tools` / tools / capability-like declarations | review evidence only; no canonical Capability IDs are granted |
| `directoryRead` | optional transport feature; not required for canonical staging |
| `resources: "dynamic"` | discoverable, but rejected for canonical intake because reproducibility cannot be proven |

The PoC intentionally creates no `SkillCapabilityRequirement` from remote tool/capability names. A
future mapping must go through the platform-owned Capability Registry and review policy.

## Deterministic candidate mapping

A fetched snapshot becomes a *candidate*, not an adopted Skill.

1. `skills/get` refreshes the exact current entry.
2. The pinned cacheable result envelope and Skill shape are validated before use.
3. Every declared file is fetched independently through the transport's normalized
   `resources/read` boundary with a hard maximum equal to its declared size.
4. The transport must abort an over-limit response before materializing an oversized body; returned
   bytes are then checked for exact declared size and SHA-256 before staging.
5. The local staged tree is hashed using the same deterministic tree digest consumed by the existing
   SkillSpector evidence path.
6. Candidate identity is a UUIDv5 derived from
   `(server identity, remote Skill URI, exact staged-tree digest)`.
7. The canonical intake profile is always `trust_status=discovered`, `enabled=false`, and
   `evaluation_status=not_evaluated`.

Consequences:

- refetching byte-identical content from the same origin produces the same candidate identity;
- the same bytes from a different server/URI remain a different candidate because origin is part of
  candidate identity and provenance;
- changed content produces a different candidate identity and immutable snapshot;
- the old snapshot remains addressable and is never overwritten;
- server deletion cannot delete an already-created canonical `SkillRevision` or `SkillBundle`;
- remote identity remains source metadata rather than silently becoming canonical identity.

The local canonical staging store is content-addressed and is not exposed as an MCP cache or virtual
mount. Origin is carried by candidate identity and provenance. If a future live MCP cache/mount is
introduced, its materialization path must additionally satisfy the extension's origin-qualified path
rule rather than reusing the canonical snapshot path as MCP-visible identity.

## Staging and retrieval safety

The PoC enforces before canonical intake:

- complete digest manifest required (`"dynamic"` is rejected for intake);
- default support for the pinned v1 512-resource / 16-MiB minimum;
- configurable file-count, individual-file and total-size policy bounds;
- transport reads capped at the declared per-file size before body materialization;
- exact byte-size verification after the bounded read;
- exact SHA-256 verification after retrieval;
- absolute resource URIs only;
- every resource must stay under the remote Skill directory and origin;
- decoded `.` / `..`, encoded slash, backslash and NUL path forms are rejected;
- case-folding path collisions are rejected for cross-platform portability;
- no archive extraction;
- no symlink creation;
- content is first written to a private temporary directory and only then atomically moved into a
  content-addressed snapshot path;
- POSIX snapshots are made read-only after publication;
- an existing content-addressed path is re-verified before reuse.

The server's claim that the manifest is complete is retained as
`manifest_completeness=server_asserted_complete`. Without `directoryRead`, the host cannot
independently enumerate undisclosed files. This uncertainty remains provenance/trust input rather
than being silently upgraded to independently verified completeness.

## Provenance and trust pipeline

Every staged candidate records:

- server identity;
- remote Skill URI;
- MCP protocol pin;
- SEP-2640 commit pin;
- `ext-skills` commit pin;
- per-snapshot tree digest;
- deterministic manifest digest;
- fetch timestamp;
- server-supplied frontmatter;
- license claim or `UNKNOWN`;
- explicit evidence that capability/tool declarations are not grants.

The candidate then enters the existing #588 lifecycle unchanged:

```text
discovered
 -> source_verified
 -> security_reviewed
 -> pilot
 -> adopted | rejected | deferred
```

The adapter has no API for skipping these transitions. A newly imported candidate is disabled and
cannot be enabled by `SkillService` before adoption plus a passed evaluation.

## SkillSpector / #868 compatibility

No MCP-specific scanner path is introduced. The adapter returns the existing
`StagedSkillCandidate` and its digest is the same deterministic tree digest validated by the
SkillSpector adapter.

The integration tests exercise the real existing `SkillSpectorSecurityEvidenceProvider` seam with a
deterministic fake container runtime:

```text
MCP fetch -> immutable staged candidate
          -> optional SkillSpector SecurityEvidence
          -> canonical trust review
```

A clean scan remains advisory evidence: the MCP candidate stays `discovered` and disabled. A partial
scan stays `DEGRADED` with its degraded reasons and likewise cannot promote trust. Scanner/provider
identity remains bound to the exact candidate digest through the existing #868 evidence model. MCP
and SkillSpector remain optional and independent.

## Update and drift semantics

| Remote event | Platform behavior |
| --- | --- |
| same server + URI + identical bytes | same deterministic candidate; existing snapshot can be reused |
| same server + URI + changed bytes/digests | new candidate/snapshot; prior history unchanged |
| listing metadata changes but fetched bytes do not | source evidence may differ; no silent canonical content rewrite |
| rename/move | different remote URI; new source identity/candidate review |
| server deletes Skill | no effect on historical canonical revisions/bundles already materialized |
| server protocol revision changes | PoC reports incompatible and does not optimistically parse |
| capability/result settings shape changes | unknown/malformed settings fail closed |
| adopted Skill has newer remote candidate | explicit review/adoption required; no auto-update |
| historical Run uses older revision | ordinary immutable #588 bundle continues to pin it |

Because `frontmatter` equality with raw `SKILL.md` would require an Agent Skills YAML parser not
currently present in the platform dependency set, the PoC treats server-returned frontmatter as
source evidence and binds canonical reproducibility to the fetched file bytes. A production adapter
must add format-conformance validation at the external-Skill intake boundary rather than trusting the
duplicate JSON representation. This known gap is another reason the PoC is not advertised as stable
MCP Skills support.

## SkillBundle reproducibility

The test suite proves the boundary rather than creating an MCP-specific bundle type:

1. an MCP-origin candidate is registered through ordinary `SkillService`;
2. it cannot be enabled while untrusted;
3. after the normal review transitions and successful evaluation, it can be enabled;
4. ordinary `SkillResolver` produces the bundle;
5. a later remote change produces a different candidate;
6. resolving the already-adopted revision again produces the same bundle digest/entries.

This keeps MCP-specific concerns out of the runtime bundle and preserves historical Run meaning if
the remote server changes or disappears.

## Compatibility matrix

| Case | Evidence/result | Claim |
| --- | --- | --- |
| deterministic local v1 fixture | automated list/get, cacheable-envelope, digest, staging and drift tests | compatible with the exact pinned PoC shape |
| pinned host-capacity limits | automated defaults test | supports 512 resources / 16 MiB minimum |
| URI/name invariant | automated top-level mismatch + nested URI tests | pinned structural rule enforced |
| server without Skills extension | automated test | explicitly unsupported; baseline Skill system unaffected |
| different MCP base revision | automated test | explicitly incompatible |
| unknown extension setting | automated test | explicitly incompatible/fail closed |
| malformed/partial result or Skill entry | automated tests | rejected as invalid provider response |
| `resources: "dynamic"` | automated test | discovery possible; canonical intake rejected as non-reproducible |
| under-declared resource body | automated bounded-read test | rejected before an oversized body can be returned to the adapter |
| SkillSpector handoff | automated clean + degraded SecurityEvidence tests | compatible with existing #868 evidence seam without trust promotion |
| current upstream/reference implementations | upstream project documents v1 implementations using list/get + per-file verification | research evidence only; no live CI conformance claim |

A live external-server test is deliberately not a required CI dependency: it would make the baseline
suite network-dependent and would still not solve the missing wire-level draft-revision witness.
When upstream ratifies the extension, add a separately marked integration/conformance job against a
pinned reference server artifact.

## Tested invariants

`tests/integration/upstreams/test_mcp_skills_interoperability.py` covers:

- exact revision constants;
- Skills capability detection;
- server without Skills;
- mismatched MCP revision;
- unknown extension setting;
- `skills/list` / `skills/get` parsing;
- exact cacheable result-envelope validation;
- malformed/partial response rejection;
- dynamic/non-reproducible content rejection;
- bounded resource reads before body materialization;
- per-file size and digest verification;
- unsafe/traversal path rejection;
- case-collision rejection;
- resource bounds;
- deterministic identical-content mapping;
- changed-content candidate separation and old-snapshot preservation;
- remote capability declarations not granting canonical capabilities;
- ordinary #588 trust lifecycle enforcement;
- ordinary SkillBundle resolution after adoption;
- bundle stability after subsequent remote drift;
- explicit partial retrieval failure.

`tests/integration/upstreams/test_mcp_skills_spec_conformance.py` adds focused coverage for:

- pinned 512-resource / 16-MiB minimum host defaults;
- URI final-segment ↔ `frontmatter.name` consistency;
- nested Skill URI handling;
- MCP numeric `ttlMs`, including explicit rejection of boolean, negative and non-finite values.

`tests/integration/upstreams/test_mcp_skills_skillspector_evidence.py` proves that an actual MCP-origin
staged candidate can enter the existing #868 provider seam and that both clean and degraded evidence
remain advisory without changing canonical Skill trust or enabled state.

## Follow-up gate

Re-open maintained implementation support when at least one of these is true:

1. SEP-2640 is ratified/stabilized with a stable protocol compatibility contract; or
2. MCP defines a reliable extension-version/conformance negotiation mechanism that lets the platform
   prove the exact semantics implemented by a connected server.

At that point the PoC must be revalidated against the new exact revision before any compatibility
constant or parser is updated. No automatic forward-compatibility assumption is allowed.
