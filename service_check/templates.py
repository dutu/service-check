from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_template(template: str, context: Mapping[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context or context[key] is None:
            return match.group(0)
        return str(context[key])

    return PLACEHOLDER_RE.sub(replace, template)

