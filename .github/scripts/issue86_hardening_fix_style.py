from pathlib import Path

path = Path("src/ai_multi_agent_platform/verification/service.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '                    "verification policy requires reviewer provider to differ from producer provider",\n',
    '                    "verification policy requires reviewer provider to differ "\n'
    '                    "from producer provider",\n',
)
path.write_text(text, encoding="utf-8")
