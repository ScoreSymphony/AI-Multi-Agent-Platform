from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    if old not in text:
        return
    target.write_text(text.replace(old, new, 1))


service = "src/ai_multi_agent_platform/control_plane/service.py"
replace_once(
    service,
    '''        for current_task_id in task_ids:
            task = await self._kernel.get_task(current_task_id)
            resources.extend(
                _run_resource(await self._kernel.get_run(current_task_id, run_id))
                for run_id in task.run_ids
            )
''',
    '''        for current_task_id in task_ids:
            task = await self._kernel.get_task(current_task_id)
            for run_id in task.run_ids:
                resources.append(
                    _run_resource(await self._kernel.get_run(current_task_id, run_id))
                )
''',
)

# Preserve the pre-existing #32 contract: model inventory is unavailable when no
# canonical ModelRegistry is configured, rather than silently presenting an empty
# registry. Issue #10 activates the native inventory only when the registry exists.
service_path = Path(service)
service_text = service_path.read_text()
service_text = service_text.replace(
    "        self._model_registry = model_registry or ModelRegistry()\n",
    "        self._model_registry = model_registry\n",
)
service_text = service_text.replace(
    "_model_provider_resource(self._model_registry, provider)",
    "_model_provider_resource(self._require_model_registry(), provider)",
)
service_text = service_text.replace(
    "_model_resource(self._model_registry, config)",
    "_model_resource(self._require_model_registry(), config)",
)
service_text = service_text.replace(
    "self._model_registry.",
    "self._require_model_registry().",
)
registry_helper = '''    def _require_model_registry(self) -> ModelRegistry:
        if self._model_registry is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical model registry is not configured",
                retryable=True,
                details={"resource": "models"},
            )
        return self._model_registry

'''
registry_anchor = "    async def _task_ids(self) -> tuple[str, ...]:\n"
if registry_helper not in service_text and registry_anchor in service_text:
    service_text = service_text.replace(registry_anchor, registry_helper + registry_anchor, 1)
service_path.write_text(service_text)

extensions = "src/ai_multi_agent_platform/control_plane/extensions.py"
replace_once(
    extensions,
    "from ai_multi_agent_platform.kernel.repository import EventRepository\n",
    "from ai_multi_agent_platform.kernel.repository import EventRepository\nfrom ai_multi_agent_platform.models import ModelRegistry\n",
)
replace_once(
    extensions,
    '    "models",\n    "providers",\n',
    '    "models",\n    "model-providers",\n    "providers",\n',
)
replace_once(
    extensions,
    '''BASE_COLLECTIONS = frozenset(
    {"projects", "workspaces", "tasks", "plans", "steps", "runs", "artifacts", "results"}
)
''',
    '''BASE_COLLECTIONS = frozenset(
    {
        "projects",
        "workspaces",
        "tasks",
        "plans",
        "steps",
        "runs",
        "artifacts",
        "results",
        "models",
        "model-providers",
    }
)
''',
)
replace_once(
    extensions,
    "        health_providers: tuple[ProviderContract, ...] = (),\n        resource_services: Mapping[str, ResourceService] | None = None,\n",
    "        health_providers: tuple[ProviderContract, ...] = (),\n        model_registry: ModelRegistry | None = None,\n        resource_services: Mapping[str, ResourceService] | None = None,\n",
)
replace_once(
    extensions,
    "            health_providers=health_providers,\n        )\n",
    "            health_providers=health_providers,\n            model_registry=model_registry,\n        )\n",
)
replace_once(
    extensions,
    '    api_exception_from_contract,\n    paginate,\n',
    '    api_exception_from_contract,\n    json_value,\n    paginate,\n',
)
replace_once(
    extensions,
    '                        "resources": list(PLATFORM_COLLECTIONS) + ["timeline"],\n                        "commands": list(REQUIRED_COMMANDS),\n',
    '                        "resources": json_value(list(PLATFORM_COLLECTIONS) + ["timeline"]),\n                        "commands": json_value(list(REQUIRED_COMMANDS)),\n',
)
replace_once(
    extensions,
    '''                resource_ref = request.body.get("resource_ref")
                if not isinstance(resource_ref, str) or not resource_ref.strip():
                    raise APIException(
                        status=400,
                        code="invalid_request",
                        message="resource_ref must be a non-blank string",
                        details={"field": "resource_ref"},
                    )
                payload = dict(request.body)
                payload.pop("resource_ref", None)
                item = await self._extended_control_plane.execute_command(
                    context,
                    segments[1],
                    resource_ref,
                    payload,
                )
''',
    '''                raw_resource_ref = request.body.get("resource_ref")
                if not isinstance(raw_resource_ref, str) or not raw_resource_ref.strip():
                    raise APIException(
                        status=400,
                        code="invalid_request",
                        message="resource_ref must be a non-blank string",
                        details={"field": "resource_ref"},
                    )
                payload = dict(request.body)
                payload.pop("resource_ref", None)
                item = await self._extended_control_plane.execute_command(
                    context,
                    segments[1],
                    raw_resource_ref,
                    payload,
                )
''',
)
