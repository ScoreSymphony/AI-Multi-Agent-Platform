# Development setup

## Requirements

- Python 3.12+
- Git

## Fresh clone

```bash
git clone https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git
cd AI-Multi-Agent-Platform
python -m venv .venv
```

Activate the virtual environment for your operating system, then install the development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Validate the repository

```bash
ruff check .
mypy
pytest
python -m build
```

These commands mirror the baseline CI checks.

## Repository boundaries

- `src/ai_multi_agent_platform/` contains platform-owned Python runtime code.
- `tests/` contains automated tests.
- `docs/` contains product and architecture documentation.
- `adapters/` is reserved for integration-specific packaging or supporting assets that should not redefine core contracts.
- `workers/` is reserved for worker-facing packages or deployment assets.
- `frontend/` is reserved for the web/control-plane client.

The exact internal domain and interface layout will be defined by the numbered architecture issues rather than guessed during bootstrap.
