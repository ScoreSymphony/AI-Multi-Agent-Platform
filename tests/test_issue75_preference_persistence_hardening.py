from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    NotificationPreference,
    RecipientRef,
    RecipientType,
    SqliteNotificationPreferenceRepository,
)


def test_runtime_preferences_survive_sqlite_restart(tmp_path: Path) -> None:
    db = tmp_path / "notifications.sqlite3"
    recipient = RecipientRef(RecipientType.USER, new_id("user"))
    first = SqliteNotificationPreferenceRepository(db)
    first.save(
        NotificationPreference(
            recipient=recipient,
            deadline_reminders_enabled=False,
            deadline_reminder_lead_seconds=3 * 60 * 60,
            overdue_reminders_enabled=False,
            quiet_hours_start="22:00",
            quiet_hours_end="07:00",
            quiet_hours_timezone="Europe/Berlin",
        )
    )

    restored = SqliteNotificationPreferenceRepository(db).get(recipient)

    assert restored.deadline_reminders_enabled is False
    assert restored.deadline_reminder_lead_seconds == 3 * 60 * 60
    assert restored.overdue_reminders_enabled is False
    assert restored.quiet_hours_start == "22:00"
    assert restored.quiet_hours_end == "07:00"
    assert restored.quiet_hours_timezone == "Europe/Berlin"


def test_existing_preference_rows_receive_backward_compatible_defaults(tmp_path: Path) -> None:
    db = tmp_path / "notifications.sqlite3"
    recipient = RecipientRef(RecipientType.USER, new_id("user"))
    repository = SqliteNotificationPreferenceRepository(db)
    legacy = {
        "recipient": {"type": recipient.type.value, "id": recipient.id},
        "enabled_categories": ["task"],
        "minimum_severity": "info",
        "project_ids": [],
        "muted": False,
        "in_app_enabled": True,
        "external_channels": [],
        "aggregate_duplicates": True,
    }
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO notification_preferences(recipient_type, recipient_id, payload)
            VALUES (?, ?, ?)
            """,
            (
                recipient.type.value,
                recipient.id,
                json.dumps(legacy, sort_keys=True, separators=(",", ":")),
            ),
        )

    restored = repository.get(recipient)

    assert restored.deadline_reminders_enabled is True
    assert restored.deadline_reminder_lead_seconds == 24 * 60 * 60
    assert restored.overdue_reminders_enabled is True
    assert restored.quiet_hours_start is None
    assert restored.quiet_hours_end is None
    assert restored.quiet_hours_timezone is None
