#!/usr/bin/env python3
"""
MCP Server for Something Awful Forums.

This server provides tools to interact with the Something Awful Forums
(forums.somethingawful.com), including reading threads and posts, browsing
forums, searching, viewing user profiles, and managing private messages.

Authentication uses your SA username and password, stored as environment
variables SA_USERNAME and SA_PASSWORD.
"""

from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from tools.auth import register_tools as register_auth_tools
from tools.forums import register_tools as register_forums_tools
from tools.threads import register_tools as register_threads_tools
from tools.search import register_tools as register_search_tools
from tools.users import register_tools as register_user_tools
from tools.pms import register_tools as register_pms_tools
from tools.usercp import register_tools as register_usercp_tools

from session import SASession


# ─────────────────────────── Session State ────────────────────────────────────

_session = SASession()

# ─────────────────────────── Lifespan ─────────────────────────────────────────


@asynccontextmanager
async def app_lifespan(server):
    """Initialize the HTTP client on startup, clean up on shutdown."""
    await _session.ensure_client()
    yield {}
    await _session.close()

# ─────────────────────────── MCP Server ───────────────────────────────────────

mcp = FastMCP("sa_forums_mcp", lifespan=app_lifespan)

mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
)

# ─────────────────────────── Tools ────────────────────────────────────────────

register_auth_tools(mcp, _session)
register_forums_tools(mcp, _session)
register_threads_tools(mcp, _session)
register_search_tools(mcp, _session)
register_user_tools(mcp, _session)
register_pms_tools(mcp, _session)
register_usercp_tools(mcp, _session)

########### main##############
if __name__ == "__main__":
    import os
    import uvicorn
    from starlette.responses import JSONResponse

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({
            "status": "ok",
            "logged_in": _session.logged_in,
            "client_ready": _session.client is not None and not _session.client.is_closed,
        })
    port = int(os.environ.get("PORT", 8080))
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=port)