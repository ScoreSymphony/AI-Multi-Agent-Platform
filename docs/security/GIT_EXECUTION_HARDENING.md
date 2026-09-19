# Git execution hardening and GitSpawn regression coverage

Issue: #1220

## Security boundary

Repository content and repository-controlled Git configuration are untrusted input. Platform-owned
Git subprocesses therefore run with a controlled Git environment and explicit command semantics
instead of inheriting arbitrary Git execution configuration from the host process.

The reference policy is intentionally narrow:

- system Git configuration is disabled;
- global Git configuration is replaced with the null file;
- controlled repository/network operations use a provider-private HOME rather than ambient user
  SSH/configuration state;
- execution-capable `GIT_*`, SSH-agent and commit-identity override variables are removed;
- Git trace variables that can redirect diagnostic output to attacker-selected paths are removed;
- relative/empty child `PATH` entries are removed so repository-relative executable lookup is unavailable;
- system attributes and replacement-object semantics are disabled for deterministic reads;
- the Git executable is resolved before the child process is launched;
- local/worktree Git configuration is inspected without following config includes;
- `.git` metadata must stay inside the managed repository root; symlinked metadata,
  escaping `commondir` targets and repository object alternates are rejected;
- execution-capable repository configuration is rejected before the requested operation;
- ordinary repository hooks are disabled through a provider-private empty hooks directory;
- external diff/textconv execution is disabled for canonical diff operations;
- implicit submodule recursion is disabled;
- external/custom Git remote-helper syntax is rejected;
- same-host filesystem remotes (`file://`, absolute or relative paths) are rejected because a
  local push can execute target-repository receive hooks on the platform host;
- Git subprocess stderr and remote URLs are not copied into canonical error diagnostics.

Canonical Authorization/Approval remains outside this adapter hardening. In particular,
`RepositoryService` continues to authorize write/network side effects such as push. The Git
subprocess boundary cannot grant authority by itself.

## Productive Git call-site inventory

### Canonical repository provider

`src/ai_multi_agent_platform/repositories/local_git.py`

Working directory:
the managed local repository root owned by the repository binding.

Repository trust:
repository files, `.git/config`, worktree config, `.gitattributes`, hooks and remote metadata
must be treated as untrusted.

Commands used:

- `init`
- `rev-parse`
- `symbolic-ref`
- `ls-tree`
- `show`
- `for-each-ref`
- `tag`
- `log`
- `status`
- `diff`
- `check-ref-format`
- `branch`
- `checkout`
- `add`
- `commit`
- `rev-list`
- `remote`
- `fetch`
- `push`

Execution context:
host subprocess owned by the local repository provider. Higher-level Agent, Control Plane,
Workspace and automation paths reach Git through canonical repository services rather than
shelling out to Git independently.

Hardening:

- controlled environment from `security.git_execution.controlled_git_environment`;
- config inspection with `git config --no-includes --name-only --null --list`;
- fail-closed rejection for execution-capable local/worktree config;
- provider-private empty `core.hooksPath`;
- `core.fsmonitor=false`;
- `commit.gpgSign=false` and `tag.gpgSign=false`;
- `submodule.recurse=false`, `fetch.recurseSubmodules=false`,
  `push.recurseSubmodules=off`;
- canonical diff uses `--no-ext-diff --no-textconv`;
- fetch/push inspect configured URLs and reject external/custom remote-helper transports and
  same-host filesystem remotes; direct `push <target>` arguments are validated with the same policy;
- raw Git stderr is used only to classify the error and is not retained in the public
  `ContractError`.

Compatibility decision:
repository-provided executable hooks, clean/smudge/process filters, external diff/textconv,
custom merge drivers, config includes, AskPass/credential helpers, executable remote overrides,
custom remote helpers, same-host filesystem remotes, repository-controlled `core.worktree`,
repository fsmonitor commands and similar execution indirection are intentionally unsupported by
the controlled local provider.
They fail before the requested operation. Normal repositories without these settings continue to
work.

### Release/upstream Git discovery

`src/ai_multi_agent_platform/release/providers.py`

Command:
`git ls-remote --exit-code <source> HEAD`.

Working directory:
none required.

Trust:
the upstream URL is external input.

Hardening:

- controlled Git environment;
- external/custom remote-helper URL syntax is rejected before spawn;
- Git executable resolution happens before child execution;
- stdin is closed;
- stderr and the raw remote URL are not included in retained discovery errors.

This path is advisory update discovery only. It does not grant repository or release authority.

### Backup build provenance

`src/ai_multi_agent_platform/backup/provenance.py`

Command:
`git rev-parse HEAD`.

Working directory:
the platform checkout selected for backup provenance.

Trust:
the checkout is an operator/platform source checkout rather than an untrusted attached
repository.

Hardening:
the probe uses the controlled Git environment and an explicitly resolved Git executable so parent
`GIT_DIR`, global/system config and executable override variables cannot silently point the
provenance probe at another checkout.

### Conformance build provenance

`src/ai_multi_agent_platform/conformance/gate.py`

Command:
`git rev-parse HEAD`.

Working directory:
the repository root used by the conformance run.

Trust:
the platform source checkout.

Hardening:
same controlled Git environment and executable resolution as backup provenance.

### Repository-intelligence ProjectAtlas candidate

`src/ai_multi_agent_platform/repositories/intelligence/projectatlas.py` does not currently run Git
directly. Its runtime probe executes only the configured absolute ProjectAtlas binary with a
minimal state-root environment. Source/index operations remain disabled, so there is currently no
productive ProjectAtlas repository Git path to admit into this regression surface.

### CLI, Control Plane, Workers, Workspaces and automation

No separate productive Git subprocess owner exists in these layers. They route repository
operations through the canonical repository provider/service boundary. Workspace repository
materialization uses canonical repository tree reads rather than `git checkout` in the target
Workspace.

This is important for ownership: adding a CLI or Worker entry point must not create a second,
less-hardened Git subprocess path.

## Non-production Git call sites

The repository also contains developer, CI, benchmark, evidence and test utilities that invoke
Git, including scripts under `scripts/ci/`, benchmark capture scripts and test fixture setup.
These are not runtime repository authorities. #1220 does not globally rewrite developer Git
configuration and the regression suite itself uses temporary repositories/config roots only.

CI/developer Git helpers remain subject to their own trust model. Any helper later reused with
untrusted repository content must first move behind the controlled Git execution boundary rather
than being promoted directly into production.

## Operation safety semantics

| Operation | Supported | Execution-capable semantics |
| --- | --- | --- |
| init | yes | controlled environment; hooks/config injection cannot come from parent `GIT_*` |
| open/rev-parse | yes | Git metadata containment and repository/worktree config audited first once a repository exists |
| status | yes | fsmonitor command disabled; unsafe repository config rejected |
| log/show/tree | yes | unsafe repository config rejected; tree materialization reads blob bytes |
| diff | yes | `--no-ext-diff --no-textconv`; unsafe diff config rejected |
| branch | yes | unsafe repository config rejected |
| checkout | yes | hooks isolated, filters/merge drivers rejected, submodule recursion disabled |
| add | yes | clean/process filters rejected before Git runs |
| commit | yes | hooks isolated, filters rejected, GPG signing disabled |
| fetch | yes | unsafe config rejected, submodule recursion disabled, remote-helper and local-filesystem URLs rejected |
| push | yes | same remote restrictions; same-host receive hooks are unreachable; canonical authorization remains mandatory above provider |
| clone | no productive local-provider path | repository lifecycle attaches/initializes managed repositories; no hidden clone subprocess |
| submodule update | intentionally unsupported | implicit recursion disabled; executable update config rejected; gitlink tree entries are not canonical file materialization |

## Malicious fixture corpus

The permanent regression corpus is
`tests/regression/security/test_gitspawn_regression.py`.

| Fixture ID | Primitive | Representative command/path | Expected result | Mitigation |
| --- | --- | --- | --- | --- |
| GS-ENV-01 | `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_*` injection | commit | sanitized/ignored | controlled Git environment |
| GS-ENV-02 | `GIT_DIR`, `GIT_WORK_TREE`, `GIT_EXEC_PATH`, external diff/SSH env | helper contract | sanitized/ignored | controlled Git environment |
| GS-ENV-03 | author/committer overrides and `GIT_TRACE*` output redirection | commit/helper contract | sanitized/ignored | controlled Git environment |
| GS-CRED-02 | ambient HOME/SSH-agent credentials | repository/discovery network operations | unavailable to child | private HOME + SSH env scrub |
| GS-GLOBAL-01 | inherited global `core.hooksPath` | commit | sanitized/ignored | null global config + private hooks path |
| GS-SYSTEM-01 | inherited `GIT_CONFIG_SYSTEM` override | commit | sanitized/ignored | nosystem + env scrub |
| GS-PATH-01 | relative/empty executable search path | controlled child environment | sanitized/ignored | absolute-only child PATH |
| GS-META-01 | symlinked `.git` metadata | open | rejected before Git operation | metadata boundary audit |
| GS-META-02 | escaping `commondir` | status | rejected before Git operation | metadata boundary audit |
| GS-OBJECT-01 | repository object alternates | status | rejected before object traversal | metadata boundary audit |
| GS-HOOK-01 | repository `.git/hooks/pre-commit` | commit | hook never executes | private empty `core.hooksPath` |
| GS-CONFIG-01 | local `core.hooksPath` / AskPass / worktree / fsmonitor | status/commit | rejected | repository config audit |
| GS-FILTER-01 | clean/smudge/process filter | add/commit | rejected before filter execution | repository config audit |
| GS-DIFF-01 | textconv/external diff config | diff | rejected or disabled | config audit + `--no-ext-diff --no-textconv` |
| GS-MERGE-01 | custom merge driver | checkout-capable path | rejected | repository config audit |
| GS-INCLUDE-01 | `include.path` / `includeIf` | status | rejected without following include | `--no-includes` audit |
| GS-CRED-01 | credential helper / AskPass / credential-file indirection | repository operation | rejected/sanitized | config audit + controlled env |
| GS-URL-01 | `url.*.insteadOf` / pushInsteadOf | repository operation | rejected | repository config audit |
| GS-REMOTE-01 | `ext::` or custom remote helper | fetch/direct push | rejected before helper spawn | remote URL validation |
| GS-REMOTE-02 | local/path remote with malicious `pre-receive` | push | rejected before target hook | remote URL validation |
| GS-SUBMODULE-01 | executable submodule update / implicit recursion | checkout/fetch | rejected/disabled | config audit + recurse=false |
| GS-DIAG-01 | secret-like remote userinfo in rejected URL | release discovery | rejected without secret echo | diagnostic minimization |
| GS-CONTROL-01 | ordinary repository | status/diff/checkout/commit | succeeds | negative control |

Every malicious payload is a harmless marker write. No fixture mutates real global or system Git
configuration.

## Evidence retention

The checked-in test names and table above retain the fixture identity, primitive, command/profile,
expected outcome and mitigation path. The CI run supplies the dynamic observation:

- platform commit: the tested pull-request merge/head SHA;
- Git version: the runner's installed Git used by the test process;
- observed result: the individual pytest result;
- attacker execution: marker-file assertions;
- sandbox/host boundary: the local provider subprocess boundary described above.

A failure is therefore attributable to a stable fixture ID/test plus a concrete CI commit and Git
runtime. No credential-bearing stderr, helper output or raw malicious remote URL is required as
evidence.

## Authorization and secret invariants

The provider hardening does not replace canonical authorization. Existing repository service tests
continue to prove that push is denied before provider side effects when the actor has only read
authority.

Secret references are not materialized into Git command arguments by the local provider. In
addition, execution-capable credential helpers/AskPass injection are rejected or removed, ambient
SSH agent sockets and user HOME configuration are not inherited by controlled repository/network
operations, and raw Git stderr is not retained in public contract errors. Release discovery uses
the same isolated credential environment and likewise does not retain the raw remote URL or stderr
on failure.

## Regression-suite isolation

The #1220 tests:

- create only temporary repositories and temporary config files;
- never run `git config --global` or `git config --system`;
- set controlled test HOME/config state for fixture setup;
- use harmless marker files to detect unexpected execution;
- prove parent-process author/committer identity cannot override the explicit provider identity;
- keep a clean-repository negative control to catch accidental blanket breakage.
