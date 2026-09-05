from pathlib import Path


deployment = Path("src/ai_multi_agent_platform/deployment/single_node.py")
text = deployment.read_text(encoding="utf-8")
start = text.index("    portability_workflow = build_agent_portability_workflow(")
end = text.index("\n\n    control_plane = ControlPlane(", start)
block = text[start:end]
if "evaluation=evaluation_composition.service" not in block:
    needle = "        templates=templates.repository,\n"
    if needle not in block:
        raise SystemExit("templates argument missing from portability block")
    block = block.replace(
        needle,
        needle + "        evaluation=evaluation_composition.service,\n",
        1,
    )
    text = text[:start] + block + text[end:]
    deployment.write_text(text, encoding="utf-8")

assets = Path("src/ai_multi_agent_platform/evaluation/suite_assets.py")
text = assets.read_text(encoding="utf-8")
old = '''                dependent = connection.execute(
                    """
                    SELECT run_id
                    FROM evaluation_runs
                    WHERE suite_id = ? AND suite_version = ?
                    LIMIT 1
                    """,
                    (str(row["suite_id"]), str(row["suite_version"])),
                ).fetchone()
'''
new = '''                history_table = connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'evaluation_runs'
                    """
                ).fetchone()
                dependent = None
                if history_table is not None:
                    dependent = connection.execute(
                        """
                        SELECT run_id
                        FROM evaluation_runs
                        WHERE suite_id = ? AND suite_version = ?
                        LIMIT 1
                        """,
                        (str(row["suite_id"]), str(row["suite_version"])),
                    ).fetchone()
'''
if old in text:
    assets.write_text(text.replace(old, new, 1), encoding="utf-8")
elif new not in text:
    raise SystemExit("expected suite rollback dependency query not found")
