from pathlib import Path

path = Path("src/ai_multi_agent_platform/control_plane/organization_api.py")
text = path.read_text(encoding="utf-8")
old = "    MembershipStatus,\n"
if text.count(old) != 1:
    raise RuntimeError(f"expected one MembershipStatus import, found {text.count(old)}")
path.write_text(text.replace(old, "", 1), encoding="utf-8")
