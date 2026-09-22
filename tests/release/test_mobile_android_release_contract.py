from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mobile-android-release.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
APP_CONFIG = ROOT / "mobile" / "app.json"
PACKAGE_CONFIG = ROOT / "mobile" / "package.json"
LOCK_CONFIG = ROOT / "mobile" / "package-lock.json"
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


def test_mobile_release_dependency_override_patches_xcode_uuid() -> None:
    package = json.loads(PACKAGE_CONFIG.read_text(encoding="utf-8"))
    lock = json.loads(LOCK_CONFIG.read_text(encoding="utf-8"))

    assert package["overrides"]["xcode"]["uuid"] == "11.1.1"
    assert lock["packages"]["node_modules/xcode"]["version"] == "3.0.1"
    assert lock["packages"]["node_modules/xcode"]["dependencies"]["uuid"] == "^7.0.3"
    assert lock["packages"]["node_modules/uuid"]["version"] == "11.1.1"


def test_rolldown_optional_platform_bindings_are_locked() -> None:
    lock = json.loads(LOCK_CONFIG.read_text(encoding="utf-8"))
    rolldown = lock["packages"]["node_modules/rolldown"]

    for package_name, version in rolldown["optionalDependencies"].items():
        locked = lock["packages"][f"node_modules/{package_name}"]
        assert locked["version"] == version
        assert locked["optional"] is True

    linux_x64 = lock["packages"]["node_modules/@rolldown/binding-linux-x64-gnu"]
    assert linux_x64["os"] == ["linux"]
    assert linux_x64["cpu"] == ["x64"]
    assert linux_x64["libc"] == ["glibc"]


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
        "npm ci --no-audit --no-fund",
        "npm run typecheck",
        "npm test",
        "npm run check:expo",
        "assembleRelease",
        "apksigner",
        "verify --verbose --print-certs",
        "SHA256SUMS.txt",
        "mobile-release.json",
        "npm-dependency-tree.json",
        '"kind": "resolved_set"',
        '"sha256": os.environ["DEPENDENCY_TREE_SHA256"]',
        "android versionCode must increase",
        "Android signing identity changed",
        "gh release create",
        "AI-Multi-Agent-Mobile-v",
    ):
        assert required in workflow

    assert "npm install --no-audit --no-fund --package-lock=false" not in workflow


def test_pull_requests_skip_native_apk_assembly_and_signing() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for step in (
        "actions/setup-java@v5",
        "Generate native Android project",
        "Keep release assembly unsigned until the signing job",
        "Assemble production release APK",
        "Verify unsigned APK metadata",
        "Upload unsigned build and provenance evidence",
    ):
        pattern = (
            rf"(?:- uses: {re.escape(step)}|- name: {re.escape(step)})\n"
            r"\s+if: github.event_name == 'workflow_dispatch'"
        )
        assert re.search(pattern, workflow)

    assert (
        "  test-signing:\n"
        "    if: github.event_name == 'workflow_dispatch'\n"
        "    needs: build"
    ) in workflow


def test_normal_ci_uses_the_same_mobile_lockfile_contract() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Install optional mobile client dependencies from committed lockfile" in workflow
    assert "working-directory: mobile" in workflow
    assert "npm ci --no-audit --no-fund" in workflow
    assert "npm install --no-audit --no-fund --package-lock=false" not in workflow


def test_release_configuration_evidence_is_valid_json_without_npm_banner() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "node scripts/validate-release-config.mjs | tee" in workflow
    assert "npm run validate:release | tee" not in workflow


def test_partial_release_publication_is_resumable_only_for_matching_draft() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for required in (
        "--json isDraft,tagName,targetCommitish",
        "Existing draft release $TAG targets a different source commit",
        'RELEASE_MODE="resume"',
        'gh release upload "$TAG"',
        "--clobber",
        'gh release create "$TAG"',
        "--draft",
        'gh release edit "$TAG"',
        "--draft=false",
        "Git tag $TAG already exists without a matching resumable draft",
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


def test_release_publication_rejects_preexisting_version_tags() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "git/matching-refs/tags/$TAG?per_page=100" in workflow
    assert "TAG_SHA" in workflow
    assert "Git tag $TAG already exists without a matching resumable draft" in workflow


def test_apk_metadata_enforces_android_7_minimum_sdk() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'EXPECTED_MIN_SDK="24"' in workflow
    assert "sdkVersion:" in workflow
    assert "APK minimum SDK mismatch" in workflow
    assert '"minimum_android_api": 24' in workflow


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
        "npm-dependency-tree.json",
        "resolved dependency",
        "apksigner",
        "mobile-production",
        "MOBILE_ANDROID_KEYSTORE_B64",
        "in-place update",
        "GitHub Releases",
        "terminal physical Android/VPS journey",
    ):
        assert required in document
