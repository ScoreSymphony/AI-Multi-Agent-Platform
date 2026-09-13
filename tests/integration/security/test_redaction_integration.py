from ai_multi_agent_platform.security import redact_text


def test_central_redaction_scrubs_sensitive_environment_assignments() -> None:
    raw = (
        "OPENAI_API_KEY=secret-value "
        "GITHUB_TOKEN=ghp_private "
        "DATABASE_PASSWORD='db-secret' "
        "SAFE_SETTING=visible"
    )

    redacted = redact_text(raw)

    assert "secret-value" not in redacted
    assert "ghp_private" not in redacted
    assert "db-secret" not in redacted
    assert "OPENAI_API_KEY=[REDACTED]" in redacted
    assert "GITHUB_TOKEN=[REDACTED]" in redacted
    assert "DATABASE_PASSWORD=[REDACTED]" in redacted
    assert "SAFE_SETTING=visible" in redacted


def test_explicit_sensitive_values_are_still_redacted() -> None:
    assert redact_text("token=abc123", ("abc123",)) == "token=[REDACTED]"
