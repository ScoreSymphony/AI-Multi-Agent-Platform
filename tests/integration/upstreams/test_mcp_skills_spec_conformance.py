from __future__ import annotations

from hashlib import sha256

import pytest

from ai_multi_agent_platform.adapters.mcp_skills import (
    PINNED_MAX_RESOURCES_PER_SKILL,
    PINNED_MAX_TOTAL_SKILL_BYTES,
    McpSkillsLimits,
    parse_list_page,
    parse_skill_entry,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode


def _entry(uri: str, name: str) -> dict[str, object]:
    return {
        "uri": uri,
        "frontmatter": {"name": name, "description": "Demo"},
        "resources": [
            {
                "uri": uri,
                "digest": f"sha256:{sha256(b'').hexdigest()}",
                "size": 0,
            }
        ],
    }


def test_default_limits_meet_pinned_v1_minimum_host_requirements() -> None:
    limits = McpSkillsLimits()

    assert PINNED_MAX_RESOURCES_PER_SKILL == 512
    assert PINNED_MAX_TOTAL_SKILL_BYTES == 16_777_216
    assert limits.max_files >= PINNED_MAX_RESOURCES_PER_SKILL
    assert limits.max_file_bytes >= PINNED_MAX_TOTAL_SKILL_BYTES
    assert limits.max_total_bytes >= PINNED_MAX_TOTAL_SKILL_BYTES


def test_skill_uri_final_path_segment_must_match_frontmatter_name() -> None:
    with pytest.raises(ContractError) as raised:
        parse_skill_entry(_entry("skill://demo/SKILL.md", "different-name"))

    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


def test_nested_skill_uri_uses_last_skill_path_segment_as_name() -> None:
    parsed = parse_skill_entry(_entry("skill://acme/billing/refunds/SKILL.md", "refunds"))

    assert parsed.uri == "skill://acme/billing/refunds/SKILL.md"
    assert parsed.frontmatter["name"] == "refunds"


def test_cacheable_ttl_accepts_non_negative_protocol_numbers() -> None:
    page = parse_list_page(
        {
            "resultType": "complete",
            "skills": [],
            "ttlMs": 0.5,
            "cacheScope": "public",
        }
    )

    assert page.skills == ()


@pytest.mark.parametrize("ttl_ms", [True, -0.1, float("inf"), float("nan")])
def test_cacheable_ttl_rejects_invalid_protocol_numbers(ttl_ms: object) -> None:
    with pytest.raises(ContractError) as raised:
        parse_list_page(
            {
                "resultType": "complete",
                "skills": [],
                "ttlMs": ttl_ms,
                "cacheScope": "private",
            }
        )

    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE
