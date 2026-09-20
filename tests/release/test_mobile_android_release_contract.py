from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mobile-android-release.yml"
APP_CONFIG = ROOT / "mobile" / "app.json"
PACKAGE_CONFIG = ROOT / "mobile" / "package.json"
GITIGNORE = ROOT / ".gitignore"
DOC = ROOT / "docs" / "operations" / "MOBILE_ANDROID_DISTRIBUTION.md"

SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$"
)


def test_mobile_android_release_identity_is_explicit() -> None:
    app = json.loads(APP_CONFIG.read_text(encoding="utf-8"))
    package = json.loads(PACKAGE_CONFIG.read_text(encoding="utf-8"))
    expo = app["expo"]

    assert SEMVER.fullmatch(expo["version"])
    assert expo["version"] == package["version"]
    assert expo["android"]["package"] == "org.scoresymphony.aimultiagentplatform"
    assert isinstance(expo["android"]["versionCode"], int)
    assert expo["android"]["versionCode"] > 0


def test_production_signing_secrets_are_publish_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    build_and_test, publish = workflow.split("\n  publish:", maxsplit=1)

    assert "pull_request_target:" not in workflow
    assert "secrets.MOBILE_ANDROID_" not in build_and_test
    assert "environment: mobile-production" in publish
    assert "contents: write" in publish
    assert "github.event_name == 'workflow_dispatch'" in publish
    assert "github.ref == 'refs/heads/main'" in publish
    assert "MOBILE_ANDROID_KEYSTORE_B64" in publish
    assert "MOBILE_ANDROID_KEYSTORE_PASSWORD" in publish
    assert "MOBILE_ANDROID_KEY_ALIAS" in publish
    assert "MOBILE_ANDROID_KEY_PASSWORD" in publish


def test_release_workflow_fails_closed_around_identity_and_integrity() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for required in (
        "npm run typecheck",
        "npm test",
        "npm run check:expo",
        "assembleRelease",
        "apksigner",
        "verify --verbose --print-certs",
        "SHA256SUMS.txt",
        "mobile-release.json",
        "android versionCode must increase",
        "Android signing identity changed",
        "gh release create",
        "AI-Multi-Agent-Mobile-v",
    ):
        assert required in workflow



def test_release_lineage_scan_paginates_all_github_releases() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "gh release list --limit 100" not in workflow
    assert "gh api --paginate" in workflow
    assert '"/repos/$GITHUB_REPOSITORY/releases?per_page=100"' in workflow
    assert "jq -sr" in workflow
    assert "tag_name" in workflow
    assert "draft == false" in workflow

def test_signing_material_and_generated_native_project_are_ignored() -> None:
    ignore = GITIGNORE.read_text(encoding="utf-8")

    for required in (
        "mobile/android/",
        "mobile/.artifacts/",
        "mobile/*.jks",
        "mobile/*.keystore",
        "mobile/*.p12",
    ):
        assert required in ignore


def test_distribution_document_covers_operator_contract() -> None:
    document = DOC.read_text(encoding="utf-8")

    for required in (
        "org.scoresymphony.aimultiagentplatform",
        "versionCode",
        "Android 7",
        "SHA256SUMS.txt",
        "apksigner",
        "mobile-production",
        "MOBILE_ANDROID_KEYSTORE_B64",
        "in-place update",
        "GitHub Releases",
        "terminal physical Android/VPS journey",
    ):
        assert required in document
