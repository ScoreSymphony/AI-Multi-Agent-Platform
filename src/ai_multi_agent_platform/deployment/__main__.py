"""Run the deployment operator CLI with ``python -m ai_multi_agent_platform.deployment``."""


def main() -> int:
    """Delegate module execution to the deployment operator CLI without import side effects."""

    from .server import main as server_main

    return server_main()


if __name__ == "__main__":
    raise SystemExit(main())
