"""Backend-neutral deterministic migration framework for issue #41."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, ContextManager, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .models import MigrationRecord, MigrationStatus, RollbackMode

MigrationAction = Callable[["MigrationContext"], None]
MigrationValidator = Callable[["MigrationContext"], None]
TransactionFactory = Callable[[], ContextManager[object]]


class MigrationError(RuntimeError):
    """Raised when migration planning or execution cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class MigrationContext:
    data_dir: Path
    metadata: Mapping[str, object] = field(default_factory=dict)
    transaction_factory: TransactionFactory = nullcontext


@dataclass(frozen=True, slots=True)
class MigrationStep:
    sequence: int
    revision: str
    from_schema: str
    to_schema: str
    description: str
    apply: MigrationAction
    validate: MigrationValidator | None = None
    transactional: bool = True
    restart_safe: bool = False
    backup_required: bool = False
    rollback_mode: RollbackMode = RollbackMode.CODE_ONLY_BEFORE_MIGRATION

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("migration sequence must be positive")
        for name, value in (
            ("revision", self.revision),
            ("from_schema", self.from_schema),
            ("to_schema", self.to_schema),
            ("description", self.description),
        ):
            if not value.strip():
                raise ValueError(f"migration {name} must be non-blank")
        if self.from_schema == self.to_schema:
            raise ValueError("migration must change the domain/schema version")
        if self.rollback_mode is RollbackMode.RESTORE_REQUIRED and not self.backup_required:
            raise ValueError("forward-only migration must require a verified backup")

    @property
    def checksum(self) -> str:
        stable = {
            "sequence": self.sequence,
            "revision": self.revision,
            "from_schema": self.from_schema,
            "to_schema": self.to_schema,
            "description": self.description,
            "transactional": self.transactional,
            "restart_safe": self.restart_safe,
            "backup_required": self.backup_required,
            "rollback_mode": self.rollback_mode.value,
        }
        payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class MigrationRegistry:
    def __init__(self, steps: tuple[MigrationStep, ...] = ()) -> None:
        ordered = tuple(sorted(steps, key=lambda item: item.sequence))
        if len({item.sequence for item in ordered}) != len(ordered):
            raise ValueError("migration registry contains duplicate sequence numbers")
        if len({item.revision for item in ordered}) != len(ordered):
            raise ValueError("migration registry contains duplicate revisions")
        self._steps = ordered

    @property
    def steps(self) -> tuple[MigrationStep, ...]:
        return self._steps

    def plan(self, current_schema: str, target_schema: str) -> tuple[MigrationStep, ...]:
        if current_schema == target_schema:
            return ()
        candidates: list[MigrationStep] = []
        schema = current_schema
        visited: set[str] = set()
        while schema != target_schema:
            if schema in visited:
                raise MigrationError("migration graph contains a cycle")
            visited.add(schema)
            matches = [step for step in self._steps if step.from_schema == schema]
            if len(matches) != 1:
                raise MigrationError(
                    f"no unambiguous supported upgrade path from schema {schema!r} "
                    f"to {target_schema!r}"
                )
            step = matches[0]
            candidates.append(step)
            schema = step.to_schema
        return tuple(candidates)


class JsonMigrationHistoryStore:
    """Append/update migration history atomically outside application database products."""

    SCHEMA_VERSION = "1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> JsonMigrationHistoryStore:
        return cls(Path(data_dir) / "db" / "migration-history.json")

    def records(self) -> tuple[MigrationRecord, ...]:
        if not self.path.exists():
            return ()
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MigrationError(f"cannot read migration history: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != self.SCHEMA_VERSION:
            raise MigrationError("unsupported migration-history document")
        entries = raw.get("records")
        if not isinstance(entries, list):
            raise MigrationError("migration history records must be an array")
        return tuple(_record_from_json(item) for item in entries)

    def get(self, revision: str) -> MigrationRecord | None:
        return next((record for record in self.records() if record.revision == revision), None)

    def put(self, record: MigrationRecord) -> None:
        records = list(self.records())
        for index, existing in enumerate(records):
            if existing.revision == record.revision:
                records[index] = record
                break
        else:
            records.append(record)
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "records": [item.to_dict() for item in records],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def unresolved_failure(self) -> MigrationRecord | None:
        failed = [item for item in self.records() if item.status is MigrationStatus.FAILED]
        return failed[-1] if failed else None


class MigrationRunner:
    def __init__(self, history: JsonMigrationHistoryStore) -> None:
        self.history = history

    def apply(
        self,
        steps: tuple[MigrationStep, ...],
        context: MigrationContext,
        *,
        resume_failed: bool = False,
    ) -> tuple[str, ...]:
        applied: list[str] = []
        for step in steps:
            existing = self.history.get(step.revision)
            if existing is not None:
                if existing.checksum != step.checksum:
                    raise MigrationError(
                        f"migration {step.revision!r} checksum changed after history was recorded"
                    )
                if existing.status is MigrationStatus.APPLIED:
                    continue
                if existing.status is MigrationStatus.FAILED:
                    if not resume_failed:
                        raise MigrationError(
                            f"migration {step.revision!r} previously failed; explicit resume required"
                        )
                    if not step.restart_safe:
                        raise MigrationError(
                            f"migration {step.revision!r} is not restart-safe; restore from backup"
                        )
            started_at = _now()
            self.history.put(
                MigrationRecord(
                    revision=step.revision,
                    checksum=step.checksum,
                    from_schema=step.from_schema,
                    to_schema=step.to_schema,
                    status=MigrationStatus.STARTED,
                    started_at=started_at,
                )
            )
            try:
                manager = context.transaction_factory() if step.transactional else nullcontext()
                with manager:
                    step.apply(context)
                    if step.validate is not None:
                        step.validate(context)
            except Exception as exc:
                self.history.put(
                    MigrationRecord(
                        revision=step.revision,
                        checksum=step.checksum,
                        from_schema=step.from_schema,
                        to_schema=step.to_schema,
                        status=MigrationStatus.FAILED,
                        started_at=started_at,
                        finished_at=_now(),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                raise MigrationError(f"migration {step.revision!r} failed: {exc}") from exc
            self.history.put(
                MigrationRecord(
                    revision=step.revision,
                    checksum=step.checksum,
                    from_schema=step.from_schema,
                    to_schema=step.to_schema,
                    status=MigrationStatus.APPLIED,
                    started_at=started_at,
                    finished_at=_now(),
                )
            )
            applied.append(step.revision)
        return tuple(applied)


def _record_from_json(value: object) -> MigrationRecord:
    if not isinstance(value, dict):
        raise MigrationError("migration history entry must be an object")

    def text(name: str, *, optional: bool = False) -> str | None:
        item = value.get(name)
        if optional and item is None:
            return None
        if not isinstance(item, str) or not item:
            raise MigrationError(f"migration history field {name!r} must be a non-empty string")
        return item

    revision = text("revision")
    checksum = text("checksum")
    from_schema = text("from_schema")
    to_schema = text("to_schema")
    started_at = text("started_at")
    assert revision is not None
    assert checksum is not None
    assert from_schema is not None
    assert to_schema is not None
    assert started_at is not None
    status_raw = text("status")
    assert status_raw is not None
    try:
        status = MigrationStatus(status_raw)
    except ValueError as exc:
        raise MigrationError(f"unsupported migration status {status_raw!r}") from exc
    return MigrationRecord(
        revision=revision,
        checksum=checksum,
        from_schema=from_schema,
        to_schema=to_schema,
        status=status,
        started_at=started_at,
        finished_at=text("finished_at", optional=True),
        error=text("error", optional=True),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
