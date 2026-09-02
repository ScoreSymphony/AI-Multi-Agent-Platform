# Development setup

## Requirements

- Python 3.12+
- Git

## Fresh clone

Clone the repository and create an isolated virtual environment:

```bash
git clone https://github.com/ScoreSymphony/AI-Multi-Agent-Platform.git
cd AI-Multi-Agent-Platform
python -m venv .venv
```

Activate the virtual environment.

Linux/macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
.venv\Scripts\activate.bat
```

Then install the development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Formatting

Format the repository before committing:

```bash
ruff format .
```

## Validate the repository

Run the complete CI-equivalent local validation path:

```bash
ruff format --check .
ruff check .
mypy
pytest
python -m build
```

GitHub Actions starts from a fresh checkout on a clean runner, creates and activates `.venv` with the same Linux/macOS bootstrap commands above, installs the same development dependencies, and then runs the validation commands in the same order. This keeps the documented fresh-clone path and CI behavior directly aligned.

## Repository boundaries

- `src/ai_multi_agent_platform/` contains platform-owned Python runtime code.
- `tests/` contains automated tests.
- `docs/` contains product and architecture documentation.
- `adapters/` is reserved for integration-specific packaging or supporting assets that should not redefine core contracts.
- `workers/` is reserved for worker-facing packages or deployment assets.
- `frontend/` is reserved for the web/control-plane client.

The exact internal domain and interface layout will be defined by the numbered architecture issues rather than guessed during bootstrap.
