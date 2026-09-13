import repository_git_cases as git_cases  # noqa: I001
import repository_run_cases as run_cases  # noqa: I001


test_secret_reference_and_external_identity_do_not_leak_local_clone_path = (
    git_cases.test_secret_reference_and_external_identity_do_not_leak_local_clone_path
)
test_missing_git_binary_is_provider_unavailable = (
    git_cases.test_missing_git_binary_is_provider_unavailable
)
test_provider_replacement_preserves_canonical_repository_identity = (
    git_cases.test_provider_replacement_preserves_canonical_repository_identity
)
test_repository_provider_exposes_common_provider_descriptor = (
    run_cases.test_repository_provider_exposes_common_provider_descriptor
)
