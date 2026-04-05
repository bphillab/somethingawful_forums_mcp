import os
from typing import Optional

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://forums.somethingawful.com"
LOGIN_URL = f"{BASE_URL}/account.php"
DEFAULT_TIMEOUT = 30.0
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