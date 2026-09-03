"""Repository-wide test configuration.

Most historical Control Plane contract tests intentionally exercise transport/domain
semantics without installing an authorization policy. Production composition is now
secure-by-default, so tests opt into that legacy unsecured mode explicitly.
"""

from __future__ import annotations

import os

os.environ.setdefault("AI_MULTI_AGENT_PLATFORM_ALLOW_INSECURE_CONTROL_PLANE", "1")
