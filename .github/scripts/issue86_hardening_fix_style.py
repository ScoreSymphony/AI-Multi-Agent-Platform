from pathlib import Path

path = Path("src/ai_multi_agent_platform/verification/service.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '                    "verification policy requires reviewer provider to differ from producer provider",\n',
    '                    "verification policy requires reviewer provider to differ "\n'
    '                    "from producer provider",\n',
)
path.write_text(text, encoding="utf-8")

evidence = Path("src/ai_multi_agent_platform/verification/evidence.py")
text = evidence.read_text(encoding="utf-8")
text = text.replace(
    "from typing import Protocol, runtime_checkable\n",
    "from typing import TYPE_CHECKING, Protocol, runtime_checkable\n",
)
text = text.replace("from ai_multi_agent_platform.kernel import PlatformKernel\n", "")
text = text.replace("from ai_multi_agent_platform.kernel.repository import EventRepository\n", "")
anchor = "from .models import ProducerIdentity, VerificationRequest, VerificationSubject\n"
replacement = (
    anchor
    + "\nif TYPE_CHECKING:\n"
    + "    from ai_multi_agent_platform.kernel import PlatformKernel\n"
    + "    from ai_multi_agent_platform.kernel.repository import EventRepository\n"
)
if "if TYPE_CHECKING:" not in text:
    text = text.replace(anchor, replacement, 1)
evidence.write_text(text, encoding="utf-8")
