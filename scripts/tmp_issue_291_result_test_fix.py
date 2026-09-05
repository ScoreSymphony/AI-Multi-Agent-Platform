from pathlib import Path

path = Path("tests/test_issue_291_verification_search.py")
text = path.read_text()

old = '''        hidden_task, hidden_request, _ = await _request(
            kernel,
            verification,
            completion,
            owner_id="bob",
            project_id=hidden_project,
            digest="sha256:hidden-verification",
        )

        rebuilt = await control_plane.rebuild_search_index()
'''
new = '''        hidden_task, hidden_request, _ = await _request(
            kernel,
            verification,
            completion,
            owner_id="bob",
            project_id=hidden_project,
            digest="sha256:hidden-verification",
        )
        hidden_result_record = verification.record_human_review(
            hidden_request.verification_id,
            reviewer_ref="user:hidden-reviewer",
            outcome=VerificationOutcome.PASS,
        )

        rebuilt = await control_plane.rebuild_search_index()
'''
if text.count(old) != 1:
    raise SystemExit("hidden verification setup insertion point not found")
text = text.replace(old, new, 1)

old = '''        hidden_result = await _search(
            http,
            type="verification_result",
            q=hidden_request.verification_id,
        )
'''
new = '''        hidden_result = await _search(
            http,
            type="verification_result",
            id=hidden_result_record.verification_result_id,
        )
'''
if text.count(old) != 1:
    raise SystemExit("hidden result lookup insertion point not found")
text = text.replace(old, new, 1)

old = '        assert hidden_request.verification_id not in serialized\n'
new = (
    '        assert hidden_request.verification_id not in serialized\n'
    '        assert hidden_result_record.verification_result_id not in serialized\n'
)
if text.count(old) != 1:
    raise SystemExit("hidden result non-disclosure assertion point not found")
text = text.replace(old, new, 1)

path.write_text(text)
