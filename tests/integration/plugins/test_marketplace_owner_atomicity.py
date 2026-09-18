from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.marketplace_owner_handlers import SkillMarketplaceKindHandler
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.distribution import (
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MarketplaceKindHandlerRegistry,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    ValidationContext,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.skills import JsonSkillRepository
from ai_multi_agent_platform.skills.codec import skill_revision_to_json
from ai_multi_agent_platform.skills.models import (
    SkillContent,
    SkillProfile,
    SkillRevision,
    SkillSource,
    SkillTrustStatus,
)
from ai_multi_agent_platform.skills.service import SkillService


class _FailOnceInstallationStore(JsonRegistryInstallationStore):
    def __init__(self, path: Path) -> None:
        self.fail_next_save = False
        super().__init__(path)

    def _save(self) -> None:
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("forced marketplace persistence failure")
        super()._save()


class _MutableSkillOwner:
    kind = RegistryItemType.SKILL

    def __init__(self) -> None:
        self.version: str | None = None
        self.fail_operation: str | None = None
        self.calls: list[str] = []

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
        return {"owner_domain": "test_skill_owner", "item_id": item.item_id}

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append("install")
        self._maybe_fail("install")
        if self.version is None:
            self.version = item.version
        elif self.version != item.version:
            raise ContractError(ErrorCode.CONFLICT, "different owner state already exists")
        return self.version

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append("update")
        self._maybe_fail("update")
        if self.version == item.version:
            return self.version
        if self.version is None:
            raise ContractError(ErrorCode.NOT_FOUND, "owner state is missing")
        self.version = item.version
        return self.version

    async def uninstall(self, item: RegistryItem) -> object:
        del item
        self.calls.append("uninstall")
        self._maybe_fail("uninstall")
        self.version = None
        return None

    async def status(self, item: RegistryItem) -> object:
        del item
        if self.version is None:
            raise ContractError(ErrorCode.NOT_FOUND, "owner state is missing")
        return self.version

    def describe(self, item: RegistryItem) -> dict[str, object]:
        del item
        return {"owner_domain": "test_skill_owner", "version": self.version}

    def _maybe_fail(self, operation: str) -> None:
        if self.fail_operation == operation:
            self.fail_operation = None
            raise RuntimeError(f"forced owner {operation} failure")


def _item(version: str) -> RegistryItem:
    return RegistryItem(
        item_id="atomicity.skill",
        item_type=RegistryItemType.SKILL,
        name="Atomicity skill",
        description="Marketplace owner atomicity fixture",
        version=version,
        publisher="tests",
        source=RegistrySource(
            "https://example.invalid/atomicity",
            f"atomicity.skill@{version}",
            revision=f"rev-{version}",
        ),
        license="MIT",
        provenance="atomicity-test",
        trust_status=TrustStatus.REVIEWED,
    )


def _service(
    tmp_path: Path,
) -> tuple[
    DistributionService,
    _FailOnceInstallationStore,
    _MutableSkillOwner,
    RegistryItem,
    RegistryItem,
    ValidationContext,
]:
    first = _item("1.0.0")
    second = _item("1.1.0")
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): b"skill-v1",
            (second.item_id, second.version): b"skill-v2",
        },
    )
    store = _FailOnceInstallationStore(tmp_path / "installations.json")
    owner = _MutableSkillOwner()
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((owner,)),
    )
    return service, store, owner, first, second, ValidationContext("0.0.1")


@pytest.mark.asyncio
async def test_owner_failures_never_advance_marketplace_installation_evidence(
    tmp_path: Path,
) -> None:
    service, store, owner, first, second, context = _service(tmp_path)

    owner.fail_operation = "install"
    with pytest.raises(RuntimeError, match="forced owner install failure"):
        await service.activate(
            service.preview(first.item_id, first.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id) is None
    assert owner.version is None

    await service.activate(
        service.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]

    owner.fail_operation = "update"
    with pytest.raises(RuntimeError, match="forced owner update failure"):
        await service.activate(
            service.preview(second.item_id, second.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == first.version

    owner.fail_operation = "uninstall"
    with pytest.raises(RuntimeError, match="forced owner uninstall failure"):
        await service.uninstall(first.item_id, authorized=True)
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == first.version


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_marketplace_state_and_retry_recovers_owner_split(
    tmp_path: Path,
) -> None:
    service, store, owner, first, second, context = _service(tmp_path)

    install_preview = service.preview(first.item_id, first.version, context)
    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(install_preview, context, authorized=True)

    assert store.get(first.item_id) is None
    assert owner.version == first.version

    await service.activate(install_preview, context, authorized=True)
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == first.version

    update_preview = service.preview(second.item_id, second.version, context)
    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(update_preview, context, authorized=True)

    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == second.version

    await service.activate(update_preview, context, authorized=True)
    assert store.get(first.item_id).current.version == second.version  # type: ignore[union-attr]
    assert owner.version == second.version

    uninstall_preview = service.preview_uninstall(first.item_id)
    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.uninstall(uninstall_preview, authorized=True)

    assert store.get(first.item_id).current.version == second.version  # type: ignore[union-attr]
    assert owner.version is None

    await service.uninstall(uninstall_preview, authorized=True)
    assert store.get(first.item_id) is None
    assert owner.version is None

    assert owner.calls == [
        "install",
        "install",
        "update",
        "update",
        "uninstall",
        "uninstall",
    ]


def _real_skill_fixture() -> tuple[RegistryItem, RegistryItem, bytes, bytes, str]:
    first = RegistryItem(
        item_id="atomicity.real-skill",
        item_type=RegistryItemType.SKILL,
        name="Atomicity real skill",
        description="Real canonical Skill owner recovery fixture",
        version="1.0.0",
        publisher="tests",
        source=RegistrySource(
            "https://example.invalid/atomicity-real-skill",
            "atomicity.real-skill@1.0.0",
            revision="source-1",
        ),
        license="MIT",
        provenance="atomicity-real-skill",
        trust_status=TrustStatus.REVIEWED,
    )
    second = replace(
        first,
        version="1.1.0",
        source=RegistrySource(
            first.source.repository,
            "atomicity.real-skill@1.1.0",
            revision="source-2",
        ),
    )
    skill_id = new_id("skill")
    owner = OwnerRef(type="user", id="atomicity-real-owner")
    first_revision = SkillRevision(
        skill_id=skill_id,
        revision=1,
        profile=SkillProfile(
            name="Atomicity real skill",
            purpose_categories=("recovery",),
            content=SkillContent(content="version one"),
            source=SkillSource(
                source_url=first.source.repository,
                source_revision="source-1",
                license="MIT",
            ),
            trust_status=SkillTrustStatus.DISCOVERED,
            enabled=False,
        ),
        owner_ref=owner,
    )
    second_revision = SkillRevision(
        skill_id=skill_id,
        revision=2,
        profile=replace(
            first_revision.profile,
            content=SkillContent(content="version two"),
        ),
        owner_ref=owner,
    )
    return (
        first,
        second,
        json.dumps(skill_revision_to_json(first_revision), sort_keys=True).encode(),
        json.dumps(skill_revision_to_json(second_revision), sort_keys=True).encode(),
        skill_id,
    )


@pytest.mark.asyncio
async def test_real_skill_owner_recovers_evidence_after_restart_for_install_update_and_uninstall(
    tmp_path: Path,
) -> None:
    first, second, artifact_v1, artifact_v2, skill_id = _real_skill_fixture()
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): artifact_v1,
            (second.item_id, second.version): artifact_v2,
        },
        provider_id="atomicity-real",
    )
    installation_path = tmp_path / "real-skill-installations.json"
    skill_path = tmp_path / "real-skills.json"
    context = ValidationContext("0.0.1")

    store = _FailOnceInstallationStore(installation_path)
    skills = SkillService(JsonSkillRepository(skill_path))
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((SkillMarketplaceKindHandler(skills),)),
    )

    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(
            service.preview(first.item_id, first.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id) is None
    assert SkillService(JsonSkillRepository(skill_path)).get_skill_revision(skill_id).revision == 1

    restarted_skills = SkillService(JsonSkillRepository(skill_path))
    restarted_store = _FailOnceInstallationStore(installation_path)
    restarted = DistributionService(
        provider,
        installations=restarted_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (SkillMarketplaceKindHandler(restarted_skills),)
        ),
    )
    await restarted.activate(
        restarted.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert restarted_store.get(first.item_id).current.version == "1.0.0"  # type: ignore[union-attr]

    update_preview = restarted.preview(second.item_id, second.version, context)
    restarted_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await restarted.activate(update_preview, context, authorized=True)
    assert restarted_store.get(first.item_id).current.version == "1.0.0"  # type: ignore[union-attr]
    assert SkillService(JsonSkillRepository(skill_path)).get_skill_revision(skill_id).revision == 2

    after_update_restart_store = _FailOnceInstallationStore(installation_path)
    after_update_restart = DistributionService(
        provider,
        installations=after_update_restart_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (SkillMarketplaceKindHandler(SkillService(JsonSkillRepository(skill_path))),)
        ),
    )
    await after_update_restart.activate(
        after_update_restart.preview(second.item_id, second.version, context),
        context,
        authorized=True,
    )
    assert (
        after_update_restart_store.get(first.item_id).current.version  # type: ignore[union-attr]
        == "1.1.0"
    )

    uninstall_preview = after_update_restart.preview_uninstall(first.item_id)
    after_update_restart_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await after_update_restart.uninstall(uninstall_preview, authorized=True)
    assert (
        after_update_restart_store.get(first.item_id).current.version  # type: ignore[union-attr]
        == "1.1.0"
    )
    with pytest.raises(ContractError) as removed_skill:
        SkillService(JsonSkillRepository(skill_path)).get_skill_revision(skill_id)
    assert removed_skill.value.code is ErrorCode.NOT_FOUND

    final_store = _FailOnceInstallationStore(installation_path)
    final_service = DistributionService(
        provider,
        installations=final_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (SkillMarketplaceKindHandler(SkillService(JsonSkillRepository(skill_path))),)
        ),
    )
    await final_service.uninstall(first.item_id, authorized=True)
    assert final_store.get(first.item_id) is None
