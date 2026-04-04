#!/usr/bin/env python3
"""
MCP Server for Something Awful Forums.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://forums.somethingawful.com"
LOGIN_URL = f"{BASE_URL}/account.php"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PER_PAGE = 40
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
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
            if "logout" in response.text.lower() or "logoutconfirm" in response.url.path:
                self.logged_in = True
                return "ok"
            soup = BeautifulSoup(response.text, "html.parser")
            error_el = soup.select_one(".error, .standard-error, #loginform .error")
            if error_el:
                return f"Error: Login failed — {error_el.get_text(strip=True)}"
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

_session = SASession()

@asynccontextmanager
async def app_lifespan(server):
    """Initialize the HTTP client on startup, clean up on shutdown."""
    await _session.ensure_client()
    yield {}
    await _session.close()


# ─────────────────────────── Health Check Server ────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    """Start health check server in background thread."""
    server = HTTPServer(("0.0.0.0", 8000), HealthHandler)
    thread = Thread(daemon=True, target=server.serve_forever)
    thread.start()
    print("Health check server started on port 8000")


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
    # Start health check server in background
    start_health_server()

    # Run MCP server on port 8080
    mcp.run(transport="streamable-http")
