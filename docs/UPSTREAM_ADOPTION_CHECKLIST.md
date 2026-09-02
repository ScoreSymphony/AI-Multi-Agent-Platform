# Upstream Adoption Checklist

Complete this review before an architecture-significant third-party component is promoted to `approved` or `integrated`.

The purpose is not to prefer external software by default. The review should make the integration mode, obligations, replacement path, and operational cost explicit before implementation commits the platform to an upstream.

## Candidate identity

- **Project name:**
- **Canonical upstream repository:**
- **Candidate version/tag/commit:**
- **Integration category/categories:**
- **Proposed platform boundary/adapter:**
- **Reviewer:**
- **Review date:**

## Required evaluation

### Functional fit

- [ ] The required capability is stated precisely.
- [ ] The upstream demonstrably provides the required capability.
- [ ] Important missing behavior or platform-specific adaptations are documented.

### Architecture fit

- [ ] The integration can remain behind platform-owned contracts.
- [ ] Canonical Task/Run/Agent/Artifact/Event/Node/Worker identities do not become upstream-private identities.
- [ ] Persistence, lifecycle, security, and distributed-node ownership remain explicit.
- [ ] The proposed integration category is the least coupled option that satisfies the need.

### Replaceability and exit

- [ ] A replacement/removal path is documented.
- [ ] Data/configuration migration requirements are understood.
- [ ] Upstream-specific behavior exposed to the rest of the platform is minimized.
- [ ] A rollback strategy exists for material updates.
- [ ] Known alternative implementations or an internal fallback are recorded where practical.

### License and provenance

- [ ] Canonical upstream location is verified.
- [ ] Candidate version/tag/commit is recorded.
- [ ] Current upstream license is verified and dated.
- [ ] Required copyright, license, and NOTICE obligations are identified.
- [ ] Material transitive/bundled license concerns are reviewed.
- [ ] Copying, vendoring, forking, or selective porting is blocked if compatibility is unclear.
- [ ] Modified upstream files can remain traceable to their source revision.

### Project health and maintenance

- [ ] Upstream activity/maintenance status is reviewed.
- [ ] Release/update cadence is understood.
- [ ] Bus factor or abandonment risk is considered where material.
- [ ] Security/advisory handling is understood where available.

### Security implications

- [ ] Required privileges and trust boundaries are understood.
- [ ] Network exposure and credential handling are documented.
- [ ] Tool execution, filesystem, process, model, and remote-code risks are assessed as applicable.
- [ ] Supply-chain implications of packages, images, plugins, extensions, or downloaded code are considered.

### Resource footprint

- [ ] CPU requirements are acceptable.
- [ ] Memory requirements are acceptable.
- [ ] GPU requirements, if any, remain capability-based rather than platform-vendor-specific.
- [ ] Storage and network requirements are acceptable.
- [ ] Expected scale limits are understood.

### Deployment complexity

- [ ] Deployment model is documented.
- [ ] Required services, ports, volumes, queues, databases, or external infrastructure are known.
- [ ] The integration can be deployed without imposing an unnecessary single-host or single-provider assumption.
- [ ] Baseline operation does not introduce a required recurring paid AI/API service.

### Dependency footprint

- [ ] Direct dependencies are understood.
- [ ] Material transitive dependencies are reviewed.
- [ ] Runtime conflicts with platform dependencies are considered.
- [ ] The component does not force unrelated platform areas onto its stack.

### API and contract stability

- [ ] Public interfaces used by the platform are identified.
- [ ] Versioning/deprecation behavior is understood.
- [ ] Adapter/contract tests can detect incompatible changes.
- [ ] Breaking upstream changes can be isolated without silently redefining canonical platform contracts.

### Simpler internal alternative

- [ ] The team considered whether the required capability can be implemented more simply inside the platform.
- [ ] The external upstream still provides enough value to justify its dependency, deployment, maintenance, or licensing cost.

## Decision

Choose one:

- [ ] **Reject** — not suitable under current requirements.
- [ ] **Reference only** — useful design influence, but no runtime/source integration.
- [ ] **Candidate** — worth further evaluation; not approved for implementation.
- [ ] **Approved** — provenance/license/architecture review complete; implementation may proceed.

### Decision rationale

Document why this integration category was chosen, why a looser boundary is or is not sufficient, and what would trigger replacement/removal.

### Required follow-up before merge

- [ ] Add/update `docs/UPSTREAMS.md` when approved or integrated.
- [ ] Create/update `upstream/PROVENANCE_TEMPLATE.yaml`-compatible metadata.
- [ ] Preserve notices for copied/modified source.
- [ ] Add adapter/contract/integration tests appropriate to the integration.
- [ ] Add/update an ADR if canonical architecture must change.
