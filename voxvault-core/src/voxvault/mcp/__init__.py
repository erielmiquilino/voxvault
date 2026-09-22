"""MCP surface: the meeting history, readable by an agent.

Importing this package stays cheap. The protocol library and the store are
pulled in only when the server is actually built, because an MCP client starts
its servers on every session and pays that cost each time.
"""

from __future__ import annotations

__all__ = ["build_server"]


def build_server(config=None):
    from .server import build_server as _build

    return _build(config)
