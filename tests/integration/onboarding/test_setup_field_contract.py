from ai_multi_agent_platform.onboarding.setup_contracts import model_setup_contract


def test_model_setup_contract_is_backend_owned_and_never_accepts_secret_values() -> None:
    contract = model_setup_contract(["openai-compatible"])

    assert contract["version"] == "1"
    assert contract["command"] == "onboarding.configure-model"
    assert contract["secret_values_accepted"] is False
    assert contract["automatic_remote_provider_selection"] is False
    assert contract["automatic_paid_provider_selection"] is False

    fields = contract["fields"]
    assert isinstance(fields, list)
    by_path = {field["path"]: field for field in fields}

    assert by_path["adapter_id"]["options"] == ["openai-compatible"]
    assert by_path["location"]["options"] == ["local", "self_hosted"]
    assert by_path["base_url"]["required"] is True

    secret_paths = {
        path for path, field in by_path.items() if field["secret_reference"] is True
    }
    assert secret_paths == {
        "credential_ref.provider",
        "credential_ref.secret_id",
        "credential_ref.scope",
        "credential_ref.version",
    }
    assert all("value" not in field for field in fields)
