from pathlib import Path

path = Path("src/ai_multi_agent_platform/compensation/repository.py")
text = path.read_text()
old = """        if existing_id is not None:
            existing = self._requests[existing_id]
            if _same_idempotency_target(existing, request):
                return existing
            raise ContractError(
                ErrorCode.CONFLICT,
                \"compensation idempotency key already belongs to another target\",
            )
        existing = self._requests.get(request.compensation_id)
        if existing is not None:
            if existing == request:
                return existing
"""
new = """        if existing_id is not None:
            existing_by_key = self._requests[existing_id]
            if _same_idempotency_target(existing_by_key, request):
                return existing_by_key
            raise ContractError(
                ErrorCode.CONFLICT,
                \"compensation idempotency key already belongs to another target\",
            )
        existing_by_id = self._requests.get(request.compensation_id)
        if existing_by_id is not None:
            if existing_by_id == request:
                return existing_by_id
"""
if new not in text:
    if old not in text:
        raise SystemExit("expected typing patch target not found")
    path.write_text(text.replace(old, new, 1))
