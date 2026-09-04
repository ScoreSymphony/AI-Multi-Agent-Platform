from __future__ import annotations

import runpy
from pathlib import Path

patcher = Path(__file__).with_name("issue86_final_attestation_patch.py")
text = patcher.read_text()
old = '''# Insert invalidation at the two return sites by operation-specific anchors.\nreplace_once(\n    "src/ai_multi_agent_platform/kernel/kernel.py",\n    "            source=source,\\n"\n    "        )\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def attach_result(\\n",\n    "            source=source,\\n"\n    "        )\\n"\n    "        self._invalidate_completion_subject(task_id)\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def attach_result(\\n",\n)\nreplace_once(\n    "src/ai_multi_agent_platform/kernel/kernel.py",\n    "            source=source,\\n"\n    "        )\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def recover_task(\\n",\n    "            source=source,\\n"\n    "        )\\n"\n    "        self._invalidate_completion_subject(task_id)\\n"\n    "        return await self.get_task(task_id)\\n\\n"\n    "    async def recover_task(\\n",\n)\n'''
new = '''# Insert invalidation at the exact attach_artifact/attach_result return sites.\nkernel_path = ROOT / "src/ai_multi_agent_platform/kernel/kernel.py"\nkernel_text = kernel_path.read_text()\nfor method_name, next_method in (("attach_artifact", "attach_result"), ("attach_result", "recover_task")):\n    start = kernel_text.index(f"    async def {method_name}(")\n    end = kernel_text.index(f"    async def {next_method}(", start)\n    block = kernel_text[start:end]\n    needle = "        return await self.get_task(task_id)\\n"\n    if needle not in block:\n        raise SystemExit(f"return anchor missing in {method_name}")\n    block = block.replace(\n        needle,\n        "        self._invalidate_completion_subject(task_id)\\n" + needle,\n        1,\n    )\n    kernel_text = kernel_text[:start] + block + kernel_text[end:]\nkernel_path.write_text(kernel_text)\n'''
if old not in text:
    raise SystemExit("kernel anchor block not found in patcher")
patcher.write_text(text.replace(old, new, 1))
runpy.run_path(str(patcher), run_name="__main__")

# Complete imports required by generated focused regressions.
authority_test = Path("tests/test_issue_86_authority_integrity.py")
authority = authority_test.read_text()
authority = authority.replace(
    "from ai_multi_agent_platform.domain import OwnerRef, new_id\n",
    "from ai_multi_agent_platform.domain import OwnerRef, TaskStatus, new_id\n",
    1,
)
authority = authority.replace(
    "from ai_multi_agent_platform.verification import (\n",
    "from ai_multi_agent_platform.verification import (\n    CompletionState,\n",
    1,
)
authority_test.write_text(authority)

hardening_test = Path("tests/test_issue_86_hardening.py")
hardening = hardening_test.read_text()
if "    VerificationCompletionAuthority,\n" not in hardening:
    hardening = hardening.replace(
        "from ai_multi_agent_platform.verification import (\n",
        "from ai_multi_agent_platform.verification import (\n    VerificationCompletionAuthority,\n",
        1,
    )
hardening_test.write_text(hardening)
