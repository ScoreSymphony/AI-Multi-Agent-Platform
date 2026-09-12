# SkillSpector pre-install SecurityEvidence

Issue #868 promotes the result of #800 into a narrowly scoped production integration. NVIDIA
SkillSpector is an **optional advisory evidence provider** for third-party Skill review. It is not a
trust, Approval, installation or activation authority.

## Supported production mode

The only supported mode is `static_no_llm_network_none`:

- SkillSpector `2.11.2`;
- upstream revision `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`;
- exact built-image identity and installed dependency-set digest recorded in
  `providers/skillspector/provider-lock.json`;
- `skillspector scan ... --no-llm`;
- container `--network=none`;
- read-only candidate mount and read-only container root filesystem;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- explicit PID, memory, CPU and tmpfs limits;
- minimal environment allowlist; provider/API credentials are not forwarded.

LLM-assisted scanning is intentionally unsupported. Enabling semantic/provider-backed analysis is a
new security/egress decision and requires a separate evaluation and policy review.

## Trust and authority boundary

`SecurityEvidenceService` owns scan orchestration and immutable evidence persistence. A provider can
produce findings, metadata and degradation reasons only. The evidence contract contains no operation
to trust, approve, install, enable, reject, delete or activate a Skill.

The authoritative lifecycle remains:

1. canonical Skill intake/source provenance;
2. optional SecurityEvidence scan;
3. human/policy review;
4. #15 Approval where required;
5. #588 trust-state transition/pilot/evaluation through the owning Skill service.

A `clean` result therefore means only that this exact scanner/configuration found no findings in this
exact candidate snapshot. It does **not** mean the Skill is trusted or safe to activate. Likewise,
provider-native `SAFE`/`DO_NOT_INSTALL` recommendations are retained as advisory provider metadata,
not translated into canonical trust state.

## Candidate boundary

The adapter accepts only a `StagedSkillCandidate`: an already acquired local directory bound to a
canonical Skill ID, revision and SHA-256 tree digest. The scanner does not fetch Git repositories,
URLs or archives. Candidate root/nested symlinks are rejected, the digest is verified before the
scan, and it is checked again afterwards.

This keeps source acquisition and provenance in the canonical Skill intake path rather than giving a
security scanner a second private acquisition lifecycle.

## Evidence retained

Each immutable `SecurityEvidence` record preserves at least:

- candidate Skill ID, revision and digest;
- provider version, source revision, image identity and dependency-set digest;
- scan mode and policy/config revision;
- observation timestamp;
- stable scanner rule ID separately from per-run finding occurrence ID;
- category, severity, confidence, path/line and provider finding metadata;
- suppression and baseline metadata;
- completeness/degradation reasons;
- network/provider usage;
- provider-native risk metadata;
- known provider limitations;
- raw-report SHA-256 and content-addressed artifact reference.

Historical evidence is append-only. Removing/disabling SkillSpector stops new scans but does not
delete prior evidence or make historical Skill revisions depend on the provider being installed.

## Failure semantics

The integration fails closed. The following are `degraded`, never `clean`:

- missing container runtime;
- provider image unavailable or image identity mismatch;
- timeout/process failure;
- scanner exit outside the supported 0/1 contract;
- missing or malformed JSON report;
- missing/failed execution status;
- missing/partial analysis completeness;
- unavailable raw-report retention;
- candidate snapshot digest changes during scanning.

Network-disabled OSV fallback/partial supply-chain analysis is deliberately represented as degraded
rather than silently treated as a successful clean scan.

## Known limitations from #800

The production review surface keeps the measured limitations visible:

- a benign negated credential-access instruction can trigger `PE3`;
- static mode missed the evaluated memory-poisoning fixture;
- file-read to network-send correlation can be incomplete;
- the evaluated autostart write did not receive a dedicated persistence finding;
- some findings overlap/duplicate semantically;
- OSV-backed dependency analysis may be partial with network disabled.

These limitations are why scanner-native scores cannot become canonical trust decisions.

## Upgrade and compatibility gate

Changing the upstream revision, production image/dependency identity, scan mode or security policy
revision requires a new review. The `SkillSpector evaluation` workflow rebuilds the exact pinned
source, verifies source/license/Dockerfile and dependency identities, runs the platform adapter tests,
and executes the #800 15-fixture corpus three times (45 scans) in network-disabled containers.

The current production pin is intentionally expected to fail that gate if dependency resolution or
the built artifact drifts. Update the pin only together with a documented re-evaluation; do not
silently loosen the check to make CI green.
