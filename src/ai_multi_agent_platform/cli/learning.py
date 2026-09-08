"""API-first CLI adapter for governed Learning Candidates (#595)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError

ConfirmAction = Callable[[argparse.Namespace, str, str], None]


def add_learning_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the governed Learning CLI surface."""

    learning = areas.add_parser(
        "learning",
        help="inspect feedback and governed Learning Candidates",
    )
    learning.set_defaults(area="learning")
    commands = learning.add_subparsers(dest="command", required=True)

    candidate = commands.add_parser("candidate", help="inspect Learning Candidates")
    candidate_commands = candidate.add_subparsers(dest="candidate_command", required=True)
    candidate_list = candidate_commands.add_parser("list", help="list Learning Candidates")
    _add_pagination_arguments(candidate_list)
    candidate_show = candidate_commands.add_parser("show", help="show one Learning Candidate")
    candidate_show.add_argument("candidate_id")

    feedback = commands.add_parser("feedback", help="inspect or record canonical feedback")
    feedback_commands = feedback.add_subparsers(dest="feedback_command", required=True)
    feedback_list = feedback_commands.add_parser("list", help="list canonical feedback")
    _add_pagination_arguments(feedback_list)
    feedback_show = feedback_commands.add_parser("show", help="show one feedback record")
    feedback_show.add_argument("feedback_id")
    feedback_create = feedback_commands.add_parser(
        "create",
        help="record feedback as evidence without granting it authority",
    )
    feedback_create.add_argument(
        "--feedback-type",
        required=True,
        choices=[
            "correction",
            "outcome_accepted",
            "outcome_rejected",
            "preference",
            "rating",
            "finding",
            "comment",
        ],
    )
    feedback_create.add_argument(
        "--subject-json",
        required=True,
        help="LearningReference JSON object",
    )
    feedback_create.add_argument("--target-json", help="optional LearningTarget JSON object")
    feedback_create.add_argument("--comment")
    feedback_create.add_argument("--project-id")
    feedback_create.add_argument("--idempotency-key")

    propose = commands.add_parser("propose", help="create an operator-authored Learning Candidate")
    propose.add_argument("--problem", required=True)
    propose.add_argument("--target-json", required=True, help="LearningTarget JSON object")
    propose.add_argument("--improvement-type", required=True)
    propose.add_argument("--expected-benefit", required=True)
    propose.add_argument(
        "--risk",
        required=True,
        choices=["standard", "elevated", "high", "critical"],
    )
    propose.add_argument("--gate-plan-json", required=True, help="LearningGatePlan JSON object")
    propose.add_argument("--source-refs-json", help="JSON array of LearningReference objects")
    propose.add_argument("--evidence-refs-json", help="JSON array of LearningReference objects")
    propose.add_argument(
        "--proposed-change-json",
        help="JSON object passed to the canonical owner adapter",
    )
    propose.add_argument("--project-id")
    propose.add_argument("--idempotency-key")

    from_feedback = commands.add_parser(
        "propose-from-feedback",
        help="create a Learning Candidate linked to exact canonical feedback",
    )
    from_feedback.add_argument("feedback_id")
    from_feedback.add_argument("--problem", required=True)
    from_feedback.add_argument("--target-json", required=True)
    from_feedback.add_argument("--improvement-type", required=True)
    from_feedback.add_argument("--expected-benefit", required=True)
    from_feedback.add_argument(
        "--risk",
        required=True,
        choices=["standard", "elevated", "high", "critical"],
    )
    from_feedback.add_argument("--gate-plan-json", required=True)
    from_feedback.add_argument("--evidence-refs-json")
    from_feedback.add_argument("--proposed-change-json")
    from_feedback.add_argument("--idempotency-key")

    evidence = commands.add_parser("evidence", help="bind Evaluation/Verification evidence")
    evidence.add_argument("candidate_id")
    evidence.add_argument("--expected-revision", type=int, required=True)
    evidence.add_argument("--evaluation-run-id", action="append", default=[])
    evidence.add_argument("--verification-id", action="append", default=[])
    evidence.add_argument("--idempotency-key")

    for name in ("accept", "reject"):
        action = commands.add_parser(name, help=f"{name} a governed Learning Candidate")
        action.add_argument("candidate_id")
        action.add_argument("--expected-revision", type=int, required=True)
        action.add_argument("--idempotency-key")

    supersede = commands.add_parser("supersede", help="supersede a Learning Candidate")
    supersede.add_argument("candidate_id")
    supersede.add_argument("--superseded-by", required=True)
    supersede.add_argument("--expected-revision", type=int, required=True)
    supersede.add_argument("--idempotency-key")

    promote = commands.add_parser(
        "promote",
        help=(
            "promote an accepted candidate through Evaluation, authorization and owner-domain gates"
        ),
    )
    promote.add_argument("candidate_id")
    promote.add_argument("--expected-revision", type=int, required=True)
    promote.add_argument("--approval-id")
    promote.add_argument("--idempotency-key")

    post_eval = commands.add_parser(
        "post-eval",
        help="inspect derived post-promotion Evaluation evidence",
    )
    post_eval_commands = post_eval.add_subparsers(dest="post_eval_command", required=True)
    post_eval_list = post_eval_commands.add_parser(
        "list",
        help="list post-promotion Evaluation records",
    )
    _add_pagination_arguments(post_eval_list)
    post_eval_show = post_eval_commands.add_parser(
        "show",
        help="show one post-promotion Evaluation record",
    )
    post_eval_show.add_argument("record_id")


def execute_learning(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: ConfirmAction,
) -> ClientResponse:
    """Execute governed Learning commands only through canonical Control Plane routes."""

    if args.command == "candidate":
        if args.candidate_command == "list":
            return client.get("/learning-candidates", query=_page_query(args))
        if args.candidate_command == "show":
            return client.get(f"/learning-candidates/{_segment(args.candidate_id)}")
        raise ProfileError(f"unsupported Learning Candidate command: {args.candidate_command}")

    if args.command == "feedback":
        if args.feedback_command == "list":
            return client.get("/learning-feedback", query=_page_query(args))
        if args.feedback_command == "show":
            return client.get(f"/learning-feedback/{_segment(args.feedback_id)}")
        if args.feedback_command == "create":
            body: dict[str, JsonValue] = {
                "resource_ref": "learning-feedback",
                "feedback_type": str(args.feedback_type),
                "subject": _json_object(args.subject_json, "--subject-json"),
            }
            if args.target_json is not None:
                body["target"] = _json_object(args.target_json, "--target-json")
            if args.comment is not None:
                body["comment"] = str(args.comment)
            if args.project_id is not None:
                body["project_id"] = str(args.project_id)
            return client.post(
                "/commands/learning.feedback.create",
                body=body,
                idempotency_key=args.idempotency_key,
            )
        raise ProfileError(f"unsupported Learning feedback command: {args.feedback_command}")

    if args.command in {"propose", "propose-from-feedback"}:
        body = {
            "resource_ref": (
                "learning-candidates" if args.command == "propose" else str(args.feedback_id)
            ),
            "problem": str(args.problem),
            "target": _json_object(args.target_json, "--target-json"),
            "improvement_type": str(args.improvement_type),
            "expected_benefit": str(args.expected_benefit),
            "risk": str(args.risk),
            "gate_plan": _json_object(args.gate_plan_json, "--gate-plan-json"),
        }
        if args.command == "propose" and args.source_refs_json is not None:
            body["source_refs"] = _json_array(args.source_refs_json, "--source-refs-json")
        if args.evidence_refs_json is not None:
            body["evidence_refs"] = _json_array(args.evidence_refs_json, "--evidence-refs-json")
        if args.proposed_change_json is not None:
            body["proposed_change"] = _json_object(
                args.proposed_change_json,
                "--proposed-change-json",
            )
        if args.command == "propose" and args.project_id is not None:
            body["project_id"] = str(args.project_id)
        command = (
            "learning.propose" if args.command == "propose" else "learning.propose-from-feedback"
        )
        return client.post(
            f"/commands/{command}",
            body=body,
            idempotency_key=args.idempotency_key,
        )

    if args.command == "evidence":
        _require_positive_revision(args.expected_revision)
        return client.post(
            "/commands/learning.evidence",
            body={
                "resource_ref": str(args.candidate_id),
                "expected_revision": args.expected_revision,
                "evaluation_run_ids": [str(value) for value in args.evaluation_run_id],
                "verification_ids": [str(value) for value in args.verification_id],
            },
            idempotency_key=args.idempotency_key,
        )

    if args.command in {"accept", "reject"}:
        _require_positive_revision(args.expected_revision)
        return client.post(
            f"/commands/learning.{args.command}",
            body={
                "resource_ref": str(args.candidate_id),
                "expected_revision": args.expected_revision,
            },
            idempotency_key=args.idempotency_key,
        )

    if args.command == "supersede":
        _require_positive_revision(args.expected_revision)
        return client.post(
            "/commands/learning.supersede",
            body={
                "resource_ref": str(args.candidate_id),
                "expected_revision": args.expected_revision,
                "superseded_by": str(args.superseded_by),
            },
            idempotency_key=args.idempotency_key,
        )

    if args.command == "promote":
        _require_positive_revision(args.expected_revision)
        confirm(args, "promote Learning Candidate", str(args.candidate_id))
        body: dict[str, JsonValue] = {
            "resource_ref": str(args.candidate_id),
            "expected_revision": args.expected_revision,
        }
        if args.approval_id is not None:
            body["approval_id"] = str(args.approval_id)
        return client.post(
            "/commands/learning.promote",
            body=body,
            idempotency_key=args.idempotency_key,
        )

    if args.command == "post-eval":
        if args.post_eval_command == "list":
            return client.get(
                "/learning-post-promotion-evaluations",
                query=_page_query(args),
            )
        if args.post_eval_command == "show":
            return client.get(f"/learning-post-promotion-evaluations/{_segment(args.record_id)}")
        raise ProfileError(f"unsupported Learning post-eval command: {args.post_eval_command}")

    raise ProfileError(f"unsupported Learning command: {args.command}")


def _json_object(raw: str, option: str) -> dict[str, JsonValue]:
    value = _json_value(raw, option)
    if not isinstance(value, dict):
        raise ProfileError(f"{option} must contain a JSON object")
    return value


def _json_array(raw: str, option: str) -> list[JsonValue]:
    value = _json_value(raw, option)
    if not isinstance(value, list):
        raise ProfileError(f"{option} must contain a JSON array")
    return value


def _json_value(raw: str, option: str) -> JsonValue:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProfileError(f"{option} must contain valid JSON") from exc


def _require_positive_revision(revision: int) -> None:
    if revision < 1:
        raise ProfileError("--expected-revision must be a positive integer")


def _add_pagination_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--q")
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--fields", help="comma-separated canonical fields")


def _page_query(args: argparse.Namespace) -> dict[str, str]:
    query = {
        "limit": str(args.limit),
        "sort": str(args.sort),
        "direction": str(args.direction),
    }
    if args.cursor:
        query["cursor"] = str(args.cursor)
    if args.q:
        query["q"] = str(args.q)
    if args.fields:
        query["fields"] = str(args.fields)
    for raw_filter in args.filter:
        field, separator, value = str(raw_filter).partition("=")
        if not separator or not field or not value:
            raise ProfileError("--filter must use FIELD=VALUE")
        query[f"filter[{field}]"] = value
    return query


def _segment(value: str) -> str:
    return quote(value, safe="")


__all__ = ["add_learning_parser", "execute_learning"]
