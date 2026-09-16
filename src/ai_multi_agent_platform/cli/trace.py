"""CLI projection for the canonical #901 Task trace explorer."""

from __future__ import annotations

import argparse

from .client import ClientResponse, ControlPlaneClient


def add_trace_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "trace",
        help="Inspect a Task multi-agent trace through the canonical Control Plane",
    )
    parser.add_argument("task_id")
    parser.add_argument("--node", help="Fetch one trace node by canonical node/span ID")
    parser.add_argument("--agent")
    parser.add_argument("--step")
    parser.add_argument("--model-config")
    parser.add_argument("--model-provider")
    parser.add_argument("--capability")
    parser.add_argument("--failure-component")
    parser.add_argument("--started-after", help="Timezone-aware ISO-8601 lower bound")
    parser.add_argument("--started-before", help="Timezone-aware ISO-8601 upper bound")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--cursor")


def execute_trace(args: argparse.Namespace, client: ControlPlaneClient) -> ClientResponse:
    task_id = str(args.task_id)
    node_id = getattr(args, "node", None)
    if node_id:
        return client.get(f"/tasks/{task_id}/trace/{node_id}")

    query = {
        "limit": str(args.limit),
        "sort": "timestamp",
        "direction": "asc",
    }
    if args.cursor:
        query["cursor"] = args.cursor
    for option, field in (
        (args.agent, "agent_id"),
        (args.step, "step_id"),
        (args.model_config, "model_config_id"),
        (args.model_provider, "model_provider_id"),
        (args.capability, "capability_id"),
        (args.failure_component, "failure_component"),
        (args.started_after, "started_after"),
        (args.started_before, "started_before"),
    ):
        if option:
            query[f"filter[{field}]"] = option
    return client.get(f"/tasks/{task_id}/trace", query=query)
