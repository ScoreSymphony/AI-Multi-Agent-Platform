"""Small provider-neutral standard Skill fixtures for examples and contract tests."""

from __future__ import annotations

from .models import SkillContent, SkillProfile


def standard_research_skill() -> SkillProfile:
    return SkillProfile(
        name="Evidence-first research",
        description="Reusable method for source-grounded research without selecting a provider.",
        purpose_categories=("research",),
        content=SkillContent(
            content=(
                "Identify the claim to establish, gather relevant evidence, "
                "distinguish source facts from inference, and preserve citations/provenance "
                "in the result."
            ),
            version="1",
        ),
        compatible_agent_roles=("researcher", "generalist"),
    )


def standard_review_skill() -> SkillProfile:
    return SkillProfile(
        name="Independent review",
        description="Reusable review method kept separate from verification policy enforcement.",
        purpose_categories=("review", "quality"),
        content=SkillContent(
            content=(
                "Review the supplied work against its stated requirements, identify concrete "
                "defects, and report evidence without silently rewriting acceptance policy."
            ),
            version="1",
        ),
        compatible_agent_roles=("reviewer", "generalist"),
    )
