from ai_multi_agent_platform.observability import MetricRecord


def test_issue171_runtime_hardening_metric_record_import_is_canonical() -> None:
    assert MetricRecord.__name__ == "MetricRecord"
