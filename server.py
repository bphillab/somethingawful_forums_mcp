#!/usr/bin/env python3
"""
MCP Server for Something Awful Forums.

This server provides tools to interact with the Something Awful Forums
(forums.somethingawful.com), including reading threads and posts, browsing
forums, searching, viewing user profiles, and managing private messages.

Authentication uses your SA username and password, stored as environment
variables SA_USERNAME and SA_PASSWORD.
"""


import json
import os
from contextlib import asynccontextmanager
from typing import Any, Optional
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# ─────────────────────────── Constants ────────────────────────────────────────


BASE_URL = "https://forums.somethingawful.com"
LOGIN_URL = f"{BASE_URL}/account.php"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PER_PAGE = 40
HEALTH_HOST = "0.0.0.0"
HEALTH_PORT = int(os.environ.get("PORT", "8080"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ─────────────────────────── Session State ────────────────────────────────────


class SASession:
    """Manages the authenticated HTTP session for Something Awful Forums."""

    def __init__(self) -> None:
        self.client: Optional[httpx.AsyncClient] = None
        self.logged_in: bool = False

    async def ensure_client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=DEFAULT_TIMEOUT,
            )
        return self.client

    async def login(self) -> str:
        """Log in with credentials from environment variables."""
        username = os.environ.get("SA_USERNAME", "")
        password = os.environ.get("SA_PASSWORD", "")

        if not username or not password:
            return (
                "Error: SA_USERNAME and SA_PASSWORD environment variables must be set. "
                "Add them to your MCP server configuration."
            )

        client = await self.ensure_client()
        try:
            response = await client.post(
                LOGIN_URL,
                data={
                    "action": "login",
                    "username": username,
                    "password": password,
                    "remember": "yes",
                    "next": "/",
                },
            )
            # Check if login was successful by looking for a logged-in indicator
            if "logout" in response.text.lower() or "logoutconfirm" in response.url.path:
                self.logged_in = True
                return "ok"
            # Check for error message in response
            soup = BeautifulSoup(response.text, "html.parser")
            error_el = soup.select_one(".error, .standard-error, #loginform .error")
            if error_el:
                return f"Error: Login failed — {error_el.get_text(strip=True)}"
            # If we were redirected away from login, assume success
            if "account.php" not in str(response.url):
                self.logged_in = True
                return "ok"
            return "Error: Login failed. Check your SA_USERNAME and SA_PASSWORD."
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} during login."
        except Exception as e:
            return f"Error: Login failed — {type(e).__name__}: {e}"

    async def get(self, url: str, **kwargs) -> httpx.Response:
        client = await self.ensure_client()
        return await client.get(url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        client = await self.ensure_client()
        return await client.post(url, **kwargs)

    async def close(self) -> None:
        if self.client and not self.client.is_closed:
            await self.client.aclose()


# Global session object, shared across requests
_session = SASession()


# ─────────────────────────── Lifespan ─────────────────────────────────────────


@asynccontextmanager
async def app_lifespan(server):
    """Initialize the HTTP client on startup, clean up on shutdown."""
    await _session.ensure_client()
    yield {}
    await _session.close()


# ─────────────────────────── Health Routes ────────────────────────────────────

async def health_endpoint(request):
    return JSONResponse({
        "status": "ok",
        "ready": True,
        "logged_in": _session.logged_in,
        "client_ready": _session.client is not None and not _session.client.is_closed,
    })

async def ready_endpoint(request):
    return JSONResponse({
        "status": "ok",
        "ready": True,
        "logged_in": _session.logged_in,
        "client_ready": _session.client is not None and not _session.client.is_closed,
    })
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

@mcp.tool()
def ready() -> dict[str, Any]:
    """Check if the server is ready to handle requests."""
    return health()


# Mount MCP at root - it will handle all other requests

# ─────────────────────────── Starlette App ────────────────────────────────────

routes = [
    Route("/health", health_endpoint),
    Route("/ready", ready_endpoint),
    Mount("/", mcp.streamable_http_app()),
]

app = Starlette(routes=routes, lifespan=app_lifespan)

# ─────────────────────────── Entry Point ──────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
