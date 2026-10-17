"""Hidden, machine-readable HTML markers on the comments Chesterton posts.

<!-- chesterton:questions pr=12 run_id=20261017T090240Z-6e9741 -->
<!-- chesterton:guard pr=13 -->
<!-- chesterton:recorded pr=12 comment=987654 -->
"""

from __future__ import annotations

import re

_MARKER = re.compile(r"<!--\s*chesterton:(?P<kind>[a-z_]+)(?P<attrs>(?:\s+[a-z_]+=[^\s>]+)*)\s*-->")
_ATTR = re.compile(r"([a-z_]+)=([^\s>]+)")


def build_marker(kind: str, **attrs: object) -> str:
    parts = " ".join(f"{k}={v}" for k, v in attrs.items() if v is not None)
    return f"<!-- chesterton:{kind}{' ' + parts if parts else ''} -->"


def marker_prefix(kind: str, pr: int) -> str:
    """Prefix that identifies a marker of `kind` for one PR (attrs after pr= may vary)."""
    return f"<!-- chesterton:{kind} pr={pr}"


def parse_markers(body: str | None) -> list[dict[str, str]]:
    """All chesterton markers in a comment body: [{"kind": ..., **attrs}]."""
    out = []
    for m in _MARKER.finditer(body or ""):
        out.append({"kind": m.group("kind"), **dict(_ATTR.findall(m.group("attrs")))})
    return out


def has_marker(body: str | None, kind: str, pr: int | None = None) -> bool:
    return any(
        m["kind"] == kind and (pr is None or m.get("pr") == str(pr)) for m in parse_markers(body)
    )
