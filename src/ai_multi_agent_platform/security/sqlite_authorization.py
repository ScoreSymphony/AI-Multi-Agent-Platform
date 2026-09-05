"""Durable SQLite policy store for the self-hosted local authorization provider."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .authorization import (
    ActorType,
    AuthorizationAction,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


class SqliteLocalAuthorizationProvider(LocalAuthorizationProvider):
    """Persist deterministic #15 local principal policies across restarts."""

    def __init__(self, path: str | Path, *, provider_id: str = "local-authorization") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._loading = True
        self._initialize_schema()
        policies = self._load_policies()
        super().__init__(policies, provider_id=provider_id)
        self._loading = False

    def register(self, policy: LocalPrincipalPolicy) -> None:
        if self._loading:
            super().register(policy)
            return
        if self.has_policy(policy.principal_ref):
            raise ValueError(
                f"authorization policy already registered for principal: {policy.principal_ref}"
            )
        self._persist_policy(policy)
        try:
            super().register(policy)
        except Exception:
            self._delete_policy(policy.principal_ref)
            raise

    def has_policy(self, principal_ref: str) -> bool:
        return principal_ref in self._policies

    def globally_grantable_actions(
        self,
        principal_ref: str,
        *,
        actor_type: str | None = None,
    ) -> frozenset[AuthorizationAction]:
        """Return only actions safely grantable without a concrete resource scope.

        Template preview needs a conservative environment-wide permission ceiling before
        canonical resources exist. Scoped policies cannot be generalized to arbitrary target
        Projects/Organizations/Teams/Workspaces, so they intentionally return no grants here.
        Approval-gated actions are also excluded: requiring approval is not equivalent to an
        immediately grantable permission. Actor type must come from trusted authentication
        context; without it the result is fail-closed.
        """

        policy = self._policies.get(principal_ref)
        if policy is None or actor_type is None:
            return frozenset()
        try:
            canonical_actor_type = ActorType(actor_type)
        except ValueError:
            return frozenset()
        if canonical_actor_type not in policy.actor_types:
            return frozenset()
        if (
            policy.project_ids
            or policy.organization_ids
            or policy.team_ids
            or policy.workspace_ids
        ):
            return frozenset()
        if policy.administrator:
            return frozenset(AuthorizationAction)
        return policy.allowed_actions

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS authorization_policies (
                    principal_ref TEXT PRIMARY KEY,
                    actor_types_json TEXT NOT NULL,
                    allowed_actions_json TEXT NOT NULL,
                    approval_actions_json TEXT NOT NULL,
                    resource_types_json TEXT NOT NULL,
                    project_ids_json TEXT NOT NULL,
                    organization_ids_json TEXT NOT NULL,
                    team_ids_json TEXT NOT NULL,
                    workspace_ids_json TEXT NOT NULL,
                    administrator INTEGER NOT NULL
                );
                """
            )

    def _load_policies(self) -> tuple[LocalPrincipalPolicy, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT principal_ref, actor_types_json, allowed_actions_json, "
                "approval_actions_json, resource_types_json, project_ids_json, "
                "organization_ids_json, team_ids_json, workspace_ids_json, administrator "
                "FROM authorization_policies ORDER BY principal_ref"
            ).fetchall()
        return tuple(
            LocalPrincipalPolicy(
                principal_ref=str(row[0]),
                actor_types=frozenset(ActorType(value) for value in _string_list(row[1])),
                allowed_actions=frozenset(
                    AuthorizationAction(value) for value in _string_list(row[2])
                ),
                approval_actions=frozenset(
                    AuthorizationAction(value) for value in _string_list(row[3])
                ),
                resource_types=frozenset(ResourceType(value) for value in _string_list(row[4])),
                project_ids=frozenset(_string_list(row[5])),
                organization_ids=frozenset(_string_list(row[6])),
                team_ids=frozenset(_string_list(row[7])),
                workspace_ids=frozenset(_string_list(row[8])),
                administrator=bool(row[9]),
            )
            for row in rows
        )

    def _persist_policy(self, policy: LocalPrincipalPolicy) -> None:
        values = (
            policy.principal_ref,
            _enum_json(policy.actor_types),
            _enum_json(policy.allowed_actions),
            _enum_json(policy.approval_actions),
            _enum_json(policy.resource_types),
            _strings_json(policy.project_ids),
            _strings_json(policy.organization_ids),
            _strings_json(policy.team_ids),
            _strings_json(policy.workspace_ids),
            int(policy.administrator),
        )
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO authorization_policies (principal_ref, actor_types_json, "
                "allowed_actions_json, approval_actions_json, resource_types_json, "
                "project_ids_json, organization_ids_json, team_ids_json, workspace_ids_json, "
                "administrator) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def _delete_policy(self, principal_ref: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM authorization_policies WHERE principal_ref = ?",
                (principal_ref,),
            )


def _enum_json(
    values: frozenset[ActorType | AuthorizationAction | ResourceType],
) -> str:
    return json.dumps(sorted(value.value for value in values))


def _strings_json(values: frozenset[str]) -> str:
    return json.dumps(sorted(values))


def _string_list(value: object) -> list[str]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("persisted authorization policy contains invalid list data")
    return parsed
