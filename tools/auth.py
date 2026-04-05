from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from models import LoginInput
from session import SASession


def register_tools(mcp: FastMCP, session: SASession) -> None:
    @mcp.tool(
        name="sa_login",
        annotations={
            "title": "Log in to Something Awful",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def sa_login(params: LoginInput = LoginInput()) -> str:
        """Log in to the Something Awful Forums using credentials from environment variables.

        Reads SA_USERNAME and SA_PASSWORD from environment variables and establishes
        an authenticated session. Must be called before using any other SA tools,
        unless the session is already active.

        The session persists for the lifetime of this MCP server process.
        """
        result = await session.login()
        if result == "ok":
            username = os.environ.get("SA_USERNAME", "unknown")
            return f"Successfully logged in to Something Awful as '{username}'."
        return result