import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPATIBILITY_PATH = ROOT / "release" / "compatibility.json"
TRACKED_UPSTREAMS = (
    ROOT / "upstream" / "hermes-agent.yaml",
    ROOT / "upstream" / "forge-ai-agent-vps.yaml",
    ROOT / "upstream" / "litellm.yaml",
)


def _quoted_field(path: Path, field: str) -> str:
    pattern = re.compile(rf'^{re.escape(field)}:\s*"([^"]+)"\s*$', re.MULTILINE)
    match = pattern.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"missing {field} in {path}"
    return match.group(1)


def test_compatibility_matrix_matches_governed_upstream_pins() -> None:
    document = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    components = document["components"]
    by_source = {item["source_url"]: item for item in components}

    for upstream_path in TRACKED_UPSTREAMS:
        source = _quoted_field(upstream_path, "canonical_upstream")
        pinned_revision = _quoted_field(upstream_path, "pinned_revision")
        assert source in by_source
        assert by_source[source]["revision"] == pinned_revision


def test_release_compatibility_metadata_has_no_floating_latest_pin() -> None:
    raw = COMPATIBILITY_PATH.read_text(encoding="utf-8").lower()
    assert '"revision": "latest"' not in raw
    assert ':latest' not in raw
