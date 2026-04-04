#!/usr/bin/env python3
"""
MCP Server for Something Awful Forums.
"""

import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# ... all your constants and SASession class (keep it the same) ...

_session = SASession()

@asynccontextmanager
async def app_lifespan(server):
    """Initialize the HTTP client on startup, clean up on shutdown."""
    await _session.ensure_client()
    yield {}
    await _session.close()

# ─────────────────────────── MCP Server ───────────────────────────────────────

mcp = FastMCP("sa_forums_mcp", lifespan=app_lifespan)

@mcp.tool()
def health() -> dict[str, Any]:
    """Check the health of the MCP server and session."""
    return {
        "status": "ok",
        "ready": True,
        "logged_in": _session.logged_in,
        "client_ready": _session.client is not None and not _session.client.is_closed,
    }

# Add your other tools here

if __name__ == "__main__":
    mcp.run(transport="streamable-http")