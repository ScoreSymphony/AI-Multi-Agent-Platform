# SkillSpector pre-install SecurityEvidence

Issue #868 promotes the #800 evaluation into a narrowly scoped production integration. NVIDIA
SkillSpector is an **optional advisory evidence provider** for third-party Skill review. It is not a
trust, Approval, installation, rejection, deletion or activation authority.

## Supported production mode

The only supported mode is `static_no_llm_network_none`:

- SkillSpector `2.11.2`;
- upstream revision `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`;
- exact dependency set captured by `providers/skillspector/requirements.lock` and the #800
  `pip-freeze.lock`;
- platform-owned production recipe `providers/skillspector/Dockerfile`;
- `skillspector scan ... --no-llm`;
- container `--network=none`;
- read-only candidate mount and read-only container root filesystem;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- explicit PID, memory, CPU and tmpfs limits;
- minimal environment allowlist; provider/API credentials are not forwarded.

LLM-assisted scanning is intentionally unsupported. Enabling semantic/provider-backed analysis is a
new security/egress decision and requires a separate evaluation and policy review.

## Why the production image is not the historical #800 image ID

#800 recorded the evaluated local image ID
`sha256:55abb78a1f1af1f430722920b96d4e24bed03e9e9811e1cf5330b859d2387fc1`. Rebuilding the same raw
upstream Dockerfile later produced a different image and already resolved a different transitive
package set. A local Docker image ID is therefore not a reproducible source-level pin when the build
recipe still resolves floating packages.

Production consequently separates two identities:

1. **Approved build manifest** — exact upstream revision, production Dockerfile digest, locked
   dependency-set digest, scan mode and policy revision. The image carries those values as labels and
   the adapter verifies them before scanning.
2. **Observed immutable OCI image ID** — the concrete image ID returned by the runtime is recorded in
   every `SecurityEvidence` record. Deployments distributing a prebuilt image may additionally set
   `expected_image_id` to require that exact image.

The historical #800 image ID remains evaluation provenance; it is not silently replaced by a later
build ID.

## Trust and authority boundary

`SecurityEvidenceService` owns scan orchestration and immutable evidence persistence. A provider can
produce findings, metadata and degradation reasons only. The evidence contract contains no operation
to trust, approve, install, enable, reject, delete or activate a Skill.

The authoritative lifecycle remains canonical Skill intake/source provenance -> optional evidence
scan -> human/policy review -> #15 Approval where required -> #588 trust-state transition and pilot.
A `clean` result therefore means only that this exact scanner/configuration found no findings in this
exact candidate snapshot. Provider-native `SAFE`/`DO_NOT_INSTALL` recommendations remain advisory
metadata and never become canonical trust state.

## Candidate boundary

The adapter accepts only a `StagedSkillCandidate`: an already acquired local directory bound to a
canonical Skill ID, revision and SHA-256 tree digest. It never fetches Git repositories, URLs or
archives. Root/nested symlinks are rejected, the digest is verified before the scan and checked again
afterwards. Candidate code is not executed by this integration.

## Evidence retained

Each immutable record preserves:

- candidate Skill ID, revision and digest;
- provider version, source revision, observed OCI image ID and dependency-set digest;
- scan mode and policy/config revision;
- observation timestamp;
- stable scanner rule ID separately from per-run finding occurrence ID;
- category, severity, confidence, path/line and provider metadata;
- suppression and baseline metadata;
- completeness/degradation reasons;
- network/provider usage;
- provider-native risk metadata;
- known provider limitations;
- raw-report SHA-256 and content-addressed artifact reference.

Historical evidence is append-only. Removing/disabling SkillSpector stops new scans but does not
delete prior evidence or make existing Skills depend on the provider being installed.

## Failure semantics

The integration fails closed. The following are `degraded`, never `clean`:

- missing container runtime;
- missing/malformed image metadata or production-label mismatch;
- optional exact image-ID mismatch;
- timeout/process failure;
- scanner exit outside the supported 0/1 contract;
- missing or malformed JSON report/findings;
- missing/partial analysis completeness;
- unavailable raw-report retention;
- candidate validation, symlink or post-scan digest failure.

Network-disabled OSV fallback/partial supply-chain analysis is deliberately represented as degraded
rather than silently treated as a successful clean scan.

## Known limitations measured in #800

The review surface keeps these limitations visible:

- a benign negated credential-access instruction can trigger `PE3`;
- static mode missed the evaluated memory-poisoning fixture;
- file-read to network-send correlation can be incomplete;
- the evaluated autostart write did not receive a dedicated persistence finding;
- some findings overlap/duplicate semantically;
- OSV-backed dependency analysis may be partial with network disabled.

These limitations are why scanner-native scores cannot become canonical trust decisions.

## Upgrade and compatibility gate

Changing the upstream revision, locked dependency set, production image recipe, scan mode or policy
revision requires a new review. The `SkillSpector evaluation` workflow verifies all immutable source
and recipe inputs, builds the locked production image, verifies its labels and installed dependency
set, runs the platform adapter tests, and executes the #800 15-fixture corpus three times (45 scans)
against that production image.

Do not loosen a pin merely to make CI green. A changed provider/dependency/recipe must be evaluated
as a new candidate and its evidence retained separately.
