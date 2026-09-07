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

- `src/ai_multi_agent_platform/` contains platform-owned Python runtime and domain code, including adapter and Worker implementations.
- `tests/` contains automated tests.
- `docs/` contains product and architecture documentation.
- `frontend/` contains the web/control-plane client.

New top-level directories should be added only when they host distinct packaging, deployment, tooling or client assets that do not belong in the platform Python package. Runtime integrations continue to implement platform-owned contracts under `src/ai_multi_agent_platform/` rather than creating parallel domain ownership.
