from __future__ import annotations

from pathlib import Path


def test_public_shell_mounts_governed_learning_routes() -> None:
    shell = Path("frontend/src/app/Shell.tsx").read_text(encoding="utf-8")

    assert 'new LearningClient({ baseUrl, fetchImpl: session.fetch })' in shell
    assert 'matchPath("/learning/:learningCandidateId", path)' in shell
    assert 'path === "/learning"' in shell
    assert '<LearningPage client={learningClient} />' in shell
    assert 'commands={learningCapabilities.commands}' in shell
    assert 'postPromotionAvailable={learningCapabilities.postPromotionAvailable}' in shell


def test_public_navigation_exposes_learning_through_canonical_collection() -> None:
    navigation = Path("frontend/src/app/navigation.ts").read_text(encoding="utf-8")

    assert (
        '{ label: "Learning", path: "/learning", group: "Intelligence", '
        'apiResource: "learning-candidates" }'
    ) in navigation


def test_public_cli_registers_and_dispatches_learning_domain() -> None:
    cli = Path("src/ai_multi_agent_platform/cli/issue_81.py").read_text(encoding="utf-8")

    assert "from .learning import add_learning_parser, execute_learning" in cli
    assert 'requested_area not in {"registry", "learning"}' in cli
    assert "add_learning_parser(areas)" in cli
    assert 'elif args.area == "learning":' in cli
    assert "execute_learning(args, client, _require_confirmation)" in cli
