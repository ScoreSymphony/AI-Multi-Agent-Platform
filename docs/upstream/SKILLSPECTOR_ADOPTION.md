# NVIDIA SkillSpector adoption review

## Decision

Adopt NVIDIA SkillSpector **optionally** as a static, pre-install `SecurityEvidence` provider for third-party Skill review. The approved production mode is intentionally narrow: SkillSpector `2.11.2` at upstream revision `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`, invoked with `--no-llm` inside a network-disabled, read-only, capability-dropped container.

SkillSpector is **not** a canonical trust, Approval, installation, enablement, rejection, deletion, lifecycle, policy, or execution authority. Canonical Skill trust state remains owned by the platform Skill lifecycle; platform authorization/Approval remains separate.

## Reviewed upstream

- Repository: `https://github.com/NVIDIA/SkillSpector`
- Version: `2.11.2`
- Commit: `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`
- License: Apache-2.0
- License verification date: 2026-09-12
- Last adoption/security review: 2026-09-12
- Evaluation issue: #800
- Integration issue: #868

The #800 evaluation established the static no-LLM baseline, deterministic fixture corpus and provider limitations. Production integration preserves that boundary rather than promoting provider-native risk recommendations to platform decisions.

## Platform boundary

The platform-owned `SecurityEvidenceProvider` protocol and immutable `SecurityEvidence` resource are canonical. The SkillSpector adapter is replaceable and may be absent without preventing Skill lifecycle operation.

The adapter accepts only an already staged local Skill snapshot bound to an exact Skill ID, immutable revision, SHA-256 candidate digest and canonical ownership scope. It does not fetch repositories, URLs or archives and does not execute candidate code.

Every persisted evidence record remains bound to the candidate Skill owner, project/workspace scope when present, revision and digest. Control Plane reads re-authorize against that scope so evidence from another tenant is not exposed by collection-level authorization alone.

## Approved execution profile

The only approved mode is `static_no_llm_network_none`:

- `skillspector scan ... --no-llm`;
- container `--network=none`;
- candidate mount read-only;
- container root filesystem read-only;
- all Linux capabilities dropped;
- `no-new-privileges` enabled;
- bounded PID, memory, CPU and tmpfs resources;
- credential-free environment allowlist;
- raw provider report retained content-addressably when available;
- timeout, crash, malformed/missing report, incomplete analysis or raw-report retention failure produce degraded/incomplete evidence, never clean evidence.

LLM-assisted scanning is not approved. Any future networked or LLM-backed mode requires a new security/egress/privacy review and a rerun of the evaluation corpus before activation.

## Reproducibility and supply chain

The production package records:

- exact upstream revision;
- verified license digest;
- upstream Dockerfile digest;
- platform-owned production Dockerfile digest;
- locked dependency set and `pip freeze` digest;
- scan mode and policy/config revision;
- observed immutable OCI image ID for each scan.

A locally observed historical image ID from #800 remains evaluation provenance; it is not treated as a reproducible source-level pin. Production verifies the locked build manifest and records the concrete OCI image identity. Deployments may additionally require an exact prebuilt image ID.

## Known limitations

The reviewed static provider has known false-positive, false-negative and overlap/noise behavior. In particular, benign negation can trigger a credential-access finding; the evaluated memory-poisoning fixture is missed in static mode; file-read/network-send correlation can be incomplete; persistence writes can lack a dedicated persistence finding; overlapping findings may occur; and network-disabled OSV supply-chain analysis can be partial. These limitations are exposed with reviewer-visible evidence.

A clean report therefore means only that this exact provider/configuration found no findings in the exact candidate snapshot. It does not imply that the Skill is safe, trusted, approved or installable.

## Update method

Upgrades are explicit review changes, never floating updates. For any provider version, upstream revision, dependency set, image recipe, scan mode or policy revision change:

1. verify upstream revision and license again;
2. review release/security changes and dependency drift;
3. rebuild the locked production image;
4. rerun the deterministic #800 corpus and negative-path integration tests;
5. review false-positive/false-negative changes and degradation semantics;
6. update `providers/skillspector/provider-lock.json`, `upstream/skillspector.yaml`, this adoption review and the upstream registry;
7. require ordinary repository CI plus the SkillSpector evaluation workflow to pass before merge.

## Exit and replacement strategy

Removal is low-coupling: unregister/disable the SkillSpector provider and remove its adapter/image package. Historical `SecurityEvidence` remains readable and immutable. Canonical Skill definitions/revisions, trust state, Approvals, Tasks, Runs and Agents require no migration. Another scanner can implement the platform-owned `SecurityEvidenceProvider` boundary without changing those domain contracts.

## Adoption result

**Approved as an optional, static, advisory pre-install evidence provider only.** It is not required for the baseline platform and requires no recurring paid service.