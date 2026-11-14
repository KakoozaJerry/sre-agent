"""Shared helpers for the read adapters.

One small helper lives here because two of the backend modules
use it and neither should import the other: bounding a piece of
text to a fixed length, marking the cut plainly when it happens.
"""

from __future__ import annotations


def _bound(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…[truncated]"
