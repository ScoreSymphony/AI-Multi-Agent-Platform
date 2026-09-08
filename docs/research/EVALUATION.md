# Research quality evaluation

Issue #589 integrates Research Evidence with the existing #19 Evaluation framework through
`ResearchEvaluationCaseExecutor`. Research does not define a second evaluator runtime and the
quality path does not require an LLM, model judge or paid service.

## Canonical evidence projection

A versioned `EvaluationCase` identifies one canonical `research_item_id`. The executor reads the
owning `ResearchRepository` and projects deterministic evidence for:

- citation coverage across Claims;
- supported, unsupported and disputed Claim counts/rates derived from current Evidence;
- current, stale, unavailable and unverifiable Evidence counts/rates;
- contradictory Evidence;
- exact SourceObservation-to-Evidence binding integrity;
- current exact-revision Verification coverage for Claims and Evidence;
- canonical Task/Run and Artifact references when present.

The executor does not call `ResearchService.assess_claim(...)` while evaluating. Evaluation is
therefore read-only with respect to mutable Claim status and measures current evidence semantics
without changing the Research record it is judging.

## Strict fixture

`canonical_research_quality_suite(research_item_id)` provides a versioned, deterministic
no-paid-service suite. Its decision-readiness case requires:

- at least one Claim and one Evidence record;
- complete citation coverage;
- all Claims supported by current non-contradicted supporting Evidence;
- zero unsupported or disputed Claims;
- zero stale, unavailable or unverifiable Evidence;
- exact Source binding integrity for every Evidence record;
- exact current #86 Verification bindings for every Claim and Evidence record.

The suite can run with the ordinary `DeterministicAssertionEvaluator` and
`MetricThresholdEvaluator`. Model-based qualitative evaluation can be layered on later through #19
without becoming canonical Research truth.

## Provider replacement

The executor consumes canonical Research records rather than acquisition-provider-private state.
Provider identity stored only in source metadata does not participate in quality scoring. Replacing
a Browser, repository-intelligence implementation, model, or another acquisition adapter therefore
does not change the Research semantic quality model when the same canonical Claim/Evidence facts
are produced.

Repository-intelligence provenance that is itself part of a canonical SourceObservation remains
visible through the Research provenance model; this is different from allowing provider-private
implementation metadata to define evaluation semantics.

## Research Team role separation

The existing standard Research Team from #77 remains an ordinary Agent Team:

- Researcher produces Claims/Evidence;
- source-checking Reviewer performs independent #86 review;
- Data Analyst remains a separate analysis role.

Research does not grant new capabilities merely because an Agent participates in that Team. The
canonical Agent runtime still applies each member's own capability policy; a Team-level request for
a capability denied by a member fails closed. The standard Researcher, Reviewer and Data Analyst
all retain their existing write/shell denials.

The #86 bridge preserves producer Agent identity on Research Claims/Evidence, so a policy with
`producer_agent_must_differ`, read-only reviewer requirements and self-verification prohibition
rejects a Researcher reviewing its own Claim while accepting the separate standard Reviewer.
