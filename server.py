#!/usr/bin/env python3
"""
MCP Server for Something Awful Forums.
"""

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional, List, Dict

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, Tag

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
    server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
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

# ─────────────────────────── Helpers ──────────────────────────────────────────


def _handle_error(e: Exception) -> str:
    """Return a clear, actionable error message."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 403:
            return "Error: Access denied (403). You may need to log in first — call sa_login."
        if code == 404:
            return "Error: Page not found (404). Check the ID or URL."
        if code == 429:
            return "Error: Rate limited (429). Wait a moment before retrying."
        return f"Error: HTTP {code} from SA Forums."
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. SA Forums may be slow — try again."
    return f"Error: {type(e).__name__}: {e}"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _require_login_msg() -> str:
    return (
        "Not logged in. Call sa_login first, or ensure SA_USERNAME "
        "and SA_PASSWORD environment variables are set."
    )


def _extract_page_count(soup: BeautifulSoup) -> int:
    """Extract total page count from SA pagination."""
    pages_el = soup.select_one(".pages, .pagenav")
    if pages_el:
        # Look for the last page number link
        page_links = pages_el.select("a")
        nums = []
        for a in page_links:
            href = a.get("href", "")
            m = re.search(r"pagenumber=(\d+)", href)
            if m:
                nums.append(int(m.group(1)))
        if nums:
            return max(nums)
    return 1


def _text(el: Optional[Tag]) -> str:
    if el is None:
        return ""
    return el.get_text(" ", strip=True)


def _attr(el: Optional[Tag], attr: str) -> str:
    if el is None:
        return ""
    return el.get(attr, "") or ""


def _clean_thread_title(text: str) -> str:
    """Normalize thread titles."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_thread_title_from_row(row: Optional[Tag]) -> str:
    """Extract the real thread title from an SA thread row."""
    if row is None:
        return ""

    # Prefer the actual title in the title cell.
    title_link = row.select_one("td.title .info a.thread_title")
    if title_link:
        title = _clean_thread_title(_text(title_link))
        if title and title.lower() != "x":
            return title

    # Next best: any thread_title link inside the row.
    for candidate in row.select("a.thread_title"):
        cls = candidate.get("class", []) or []
        title = _clean_thread_title(_text(candidate))
        if not title or title.lower() == "x":
            continue
        if "x" in cls:
            continue
        return title

    return ""


def _extract_unread_link_from_row(row: Optional[Tag]) -> str:
    """Extract the goto=newpost link for a thread row, if present."""
    if row is None:
        return ""

    unread_link = row.select_one(".lastseen a.count[href*='goto=newpost']")
    if unread_link:
        href = _attr(unread_link, "href")
        if href:
            return f"{BASE_URL}/{href.lstrip('/')}"
    return ""


def _extract_unread_count_from_row(row: Optional[Tag]) -> int:
    """Extract unread count for a thread row, if present."""
    if row is None:
        return 0

    unread_count_el = row.select_one(".lastseen a.count b, .lastseen a.count")
    unread_count_text = _text(unread_count_el).replace(",", "")
    if unread_count_text.isdigit():
        return int(unread_count_text)
    return 0


def _extract_last_page_url_from_row(row: Optional[Tag]) -> str:
    """Extract a last-page or last-post URL from a thread row, if present."""
    if row is None:
        return ""

    # Prefer an explicit "Last post" jump if SA provides it.
    lastpost_link = row.select_one(".title_pages a[href*='goto=lastpost']")
    if lastpost_link:
        href = _attr(lastpost_link, "href")
        if href:
            return f"{BASE_URL}/{href.lstrip('/')}"

    # Otherwise use the highest page number shown in the thread row.
    page_links = row.select(".title_pages a[href*='pagenumber=']")
    last_page_num = 0
    last_page_href = ""
    for a in page_links:
        href = _attr(a, "href")
        m = re.search(r"pagenumber=(\d+)", href)
        if m:
            page_num = int(m.group(1))
            if page_num > last_page_num:
                last_page_num = page_num
                last_page_href = href

    if last_page_href:
        return f"{BASE_URL}/{last_page_href.lstrip('/')}"

    return ""

# ─────────────────────────── Pydantic Models ──────────────────────────────────


class LoginInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    # No fields — credentials come from env vars


class ListForumsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class ListThreadsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    forum_id: int = Field(
        ...,
        description="The SA forum ID (e.g. 46 for FYAD). Find IDs via sa_list_forums.",
        ge=1,
    )
    page: int = Field(default=1, description="Page number to fetch", ge=1)
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class GetThreadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    thread_id: int = Field(
        ...,
        description="The SA thread ID. Found in thread URLs as threadid=X.",
        ge=1,
    )
    page: int = Field(default=1, description="Page of posts to fetch", ge=1)
    last_page: bool = Field(
        default=False,
        description="If true, fetch the thread's last page instead of the page in 'page'.",
    )
    goto_newpost: bool = Field(
        default=False,
        description="If true, follow the thread's goto=newpost link and fetch the page containing the first unread post.",
    )
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class SearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(
        ...,
        description=(
            "Search terms to find in SA threads and posts. "
            "Supports SA search operators: intitle:word, username:goon, "
            "since:YYYY-MM-DD, before:YYYY-MM-DD, threadid:12345, quoting:goon."
        ),
        min_length=1,
        max_length=300,
    )
    forum_id: Optional[int] = Field(
        default=None,
        description="Restrict search to a specific forum ID. Leave blank to search all forums.",
        ge=1,
    )
    user: Optional[str] = Field(
        default=None,
        description=(
            "Restrict search to posts by this SA username. "
            "Appended to the query as the username: operator."
        ),
        max_length=100,
    )
    page: int = Field(default=1, description="Page of results to fetch", ge=1)
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class GetUserInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    username: Optional[str] = Field(
        default=None,
        description="SA username to look up. Provide either username or user_id.",
        max_length=100,
    )
    user_id: Optional[int] = Field(
        default=None,
        description="SA user ID to look up. Provide either username or user_id.",
        ge=1,
    )
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class ListPMsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    folder_id: int = Field(
        default=0,
        description="PM folder ID. 0 = Inbox (default), 1 = Sent, other numbers for custom folders.",
        ge=0,
    )
    page: int = Field(default=1, description="Page of messages to fetch", ge=1)
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class GetPMInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    pm_id: int = Field(
        ...,
        description="Private message ID. Found via sa_list_pms.",
        ge=1,
    )
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class ListUserCPThreadsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


# ─────────────────────────── Tools ────────────────────────────────────────────


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
async def sa_login(params: LoginInput=LoginInput()) -> str:
    """Log in to the Something Awful Forums using credentials from environment variables.

    Reads SA_USERNAME and SA_PASSWORD from environment variables and establishes
    an authenticated session. Must be called before using any other SA tools,
    unless the session is already active.

    The session persists for the lifetime of this MCP server process.

    Args:
        params (LoginInput): No fields required — credentials come from SA_USERNAME
            and SA_PASSWORD environment variables.

    Returns:
        str: Success message on login, or an error string starting with "Error:".

    Examples:
        - Call sa_login before browsing forums, searching, or reading PMs.
        - If you get 403 errors, call sa_login again to refresh the session.
    """
    result = await _session.login()
    if result == "ok":
        username = os.environ.get("SA_USERNAME", "unknown")
        return f"Successfully logged in to Something Awful as '{username}'."
    return result


@mcp.tool(
    name="sa_list_forums",
    annotations={
        "title": "List SA Forums",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_list_forums(params: ListForumsInput) -> str:
    """List all available forums on Something Awful.

    Fetches the forum index page and returns all forum categories and their
    sub-forums with names and IDs. Use the forum IDs with sa_list_threads.

    Args:
        params (ListForumsInput): Input parameters containing:
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        str: Forum list in the requested format.

        Markdown format:
            # Something Awful Forums
            ## Category Name
            - **Forum Name** (ID: 123) — description

        JSON format:
        [
          {
            "category": "Category Name",
            "forums": [
              {"id": 123, "name": "Forum Name", "description": "...", "threads": 1234, "posts": 56789}
            ]
          }
        ]

        Error response: "Error: <message>"

    Examples:
        - Use before sa_list_threads to find the forum ID you want.
        - "List all forums" → call with default params
    """
    try:
        resp = await _session.get(f"{BASE_URL}/index.php")
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    soup = _soup(resp.text)
    forums_table = soup.select_one("table#forums")
    if not forums_table:
        return "No forums found. The SA index layout may have changed."

    categories: List[Dict[str, Any]] = []
    current_category = "General"
    current_forums: List[Dict[str, Any]] = []

    for row in forums_table.select("tr"):
        category_el = row.select_one("th.category")
        if category_el:
            if current_forums:
                categories.append(
                    {"category": current_category, "forums": current_forums}
                )
                current_forums = []
            current_category = _text(category_el)
            continue

        forum_link = row.select_one("a.forum[href*='forumdisplay.php']")
        if not forum_link:
            continue

        href = _attr(forum_link, "href")
        fid_match = re.search(r"forumid=(\d+)", href)
        if not fid_match:
            continue

        fid = int(fid_match.group(1))
        fname = _text(forum_link)

        desc_el = row.select_one("span.forumdesc")
        fdesc = _text(desc_el).lstrip(" -").strip() if desc_el else ""

        current_forums.append(
            {
                "id": fid,
                "name": fname,
                "description": fdesc,
                "threads": "",
                "posts": "",
            }
        )

    if current_forums:
        categories.append({"category": current_category, "forums": current_forums})

    if not categories:
        return (
            "No forums found. If you're not logged in, try sa_login first — "
            "some forum categories are only visible to registered users."
        )

    if params.response_format == "json":
        return json.dumps(categories, indent=2)

    # Markdown
    lines = ["# Something Awful Forums\n"]
    for cat in categories:
        lines.append(f"## {cat['category']}\n")
        for f in cat["forums"]:
            desc = f" — {f['description']}" if f["description"] else ""
            counts = ""
            if f["threads"]:
                counts += f" | {f['threads']} threads"
            if f["posts"]:
                counts += f" | {f['posts']} posts"
            lines.append(f"- **{f['name']}** (ID: {f['id']}){desc}{counts}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(
    name="sa_list_threads",
    annotations={
        "title": "List Threads in a Forum",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_list_threads(params: ListThreadsInput) -> str:
    """List threads in a specific Something Awful forum.

    Fetches a page of threads from the specified forum. Use the thread IDs
    with sa_get_thread to read posts.

    Args:
        params (ListThreadsInput): Input parameters containing:
            - forum_id (int): Forum ID from sa_list_forums (required)
            - page (int): Page number to fetch (default: 1)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        str: Thread list in the requested format.

        Markdown format:
            # Threads in Forum 123 (page 1 of N)
            ## Thread Title (ID: 456)
            - **Author**: username | **Replies**: 42 | **Last post**: date by user

        JSON format:
        {
          "forum_id": 123,
          "forum_name": "Forum Name",
          "page": 1,
          "total_pages": 5,
          "threads": [
            {
              "id": 456,
              "title": "Thread Title",
              "author": "username",
              "replies": 42,
              "views": 1000,
              "last_post_date": "...",
              "last_poster": "username",
              "sticky": false,
              "locked": false
            }
          ]
        }

        Error response: "Error: <message>"

    Examples:
        - "Show me threads in GBS" → find GBS forum ID via sa_list_forums first
        - "What's on page 3 of BYOB?" → use forum_id for BYOB, page=3
    """
    url = (
        f"{BASE_URL}/forumdisplay.php"
        f"?forumid={params.forum_id}&perpage={DEFAULT_PER_PAGE}&pagenumber={params.page}"
    )
    try:
        resp = await _session.get(url)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    soup = _soup(resp.text)

    # Forum name
    forum_name_el = soup.select_one("h1, .forum-name, title")
    forum_name = _text(forum_name_el).replace(" - Something Awful Forums", "").strip()

    total_pages = _extract_page_count(soup)

    threads: List[Dict[str, Any]] = []

    # Threads are typically in a <table id="forum"> or similar
    thread_rows = soup.select(
        "table#forum tr.thread, "
        "tr.thread, "
        "tr[id^='thread'], "
        ".threadlist tr:not(:first-child)"
    )

    for row in thread_rows:
        # Thread link / ID
        link = row.select_one("a.thread_title, a[href*='showthread.php']")
        if not link:
            continue
        href = _attr(link, "href")
        tid_match = re.search(r"threadid=(\d+)", href)
        if not tid_match:
            continue

        tid = int(tid_match.group(1))
        title = _extract_thread_title_from_row(row)

        unread_url = _extract_unread_link_from_row(row)
        unread_count = _extract_unread_count_from_row(row)

        # Author
        author_el = row.select_one(".author, td.author, .threadauthor")
        author = _text(author_el)

        # Reply count
        reply_el = row.select_one(".replies, td.replies, .replycount")
        replies_text = _text(reply_el).replace(",", "")
        try:
            replies = int(replies_text)
        except ValueError:
            replies = 0

        # Views
        view_el = row.select_one(".views, td.views")
        views_text = _text(view_el).replace(",", "")
        try:
            views = int(views_text)
        except ValueError:
            views = 0

        # Last post info
        last_post_el = row.select_one(".lastpost, td.lastpost, .lastpostinfo")
        last_post_text = _text(last_post_el)
        last_post_date = ""
        last_poster = ""
        if last_post_el:
            date_el = last_post_el.select_one(".date")
            user_el = last_post_el.select_one("a")
            last_post_date = _text(date_el)
            last_poster = _text(user_el)
            if not last_post_date:
                last_post_date = last_post_text

        # Flags
        sticky = bool(row.select_one(".sticky, .icon-sticky"))
        locked = bool(row.select_one(".locked, .icon-lock"))

        threads.append(
            {
                "id": tid,
                "title": title,
                "author": author,
                "replies": replies,
                "views": views,
                "last_post_date": last_post_date,
                "last_poster": last_poster,
                "sticky": sticky,
                "locked": locked,
            }
        )

    if not threads:
        return (
            f"No threads found in forum {params.forum_id} page {params.page}. "
            "Make sure you're logged in (sa_login) and the forum ID is correct."
        )

    if params.response_format == "json":
        return json.dumps(
            {
                "forum_id": params.forum_id,
                "forum_name": forum_name,
                "page": params.page,
                "total_pages": total_pages,
                "threads": threads,
            },
            indent=2,
        )

    lines = [f"# Threads in {forum_name or f'Forum {params.forum_id}'} (page {params.page} of {total_pages})\n"]
    for t in threads:
        flags = ""
        if t["sticky"]:
            flags += " 📌"
        if t["locked"]:
            flags += " 🔒"
        lines.append(f"## {t['title']}{flags} (ID: {t['id']})")
        meta = f"- **Author**: {t['author'] or 'unknown'} | **Replies**: {t['replies']}"
        if t["last_poster"]:
            meta += f" | **Last post**: {t['last_post_date']} by {t['last_poster']}"
        lines.append(meta)
        lines.append("")
    return "\n".join(lines)


@mcp.tool(
    name="sa_get_thread",
    annotations={
        "title": "Read Thread Posts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_get_thread(params: GetThreadInput) -> str:
    """Read posts from a Something Awful thread.

    Fetches a page of posts from the specified thread. Posts include author,
    timestamp, post ID, and the full text content (HTML tags stripped).

    Args:
        params (GetThreadInput): Input parameters containing:
            - thread_id (int): Thread ID from sa_list_threads (required)
            - page (int): Page of posts to fetch (default: 1)
            - last_page (bool): If true, fetch the last page
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        str: Posts in the requested format.
    """
    if params.goto_newpost:
        url = (
            f"{BASE_URL}/showthread.php"
            f"?threadid={params.thread_id}&goto=newpost"
        )
    elif params.last_page:
        url = (
            f"{BASE_URL}/showthread.php"
            f"?threadid={params.thread_id}&perpage={DEFAULT_PER_PAGE}&pagenumber=999999"
        )
    else:
        url = (
            f"{BASE_URL}/showthread.php"
            f"?threadid={params.thread_id}&perpage={DEFAULT_PER_PAGE}&pagenumber={params.page}"
        )
    try:
        resp = await _session.get(url)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    soup = _soup(resp.text)

    # Thread title
    title_el = soup.select_one("title, h1, .thread-title")
    thread_title = _text(title_el).replace(" - Something Awful Forums", "").strip()

    total_pages = _extract_page_count(soup)
    effective_page = total_pages if params.last_page else params.page

    posts: List[Dict[str, Any]] = []

    # SA posts are in <table class="post"> elements
    post_tables = soup.select("table.post, div.post, .postbody, tr.post")

    # Also try the common SA structure: table with id starting with "post"
    if not post_tables:
        post_tables = soup.select("[id^='post']")

    for post_el in post_tables:
        # Post ID
        post_id_str = post_el.get("id", "") or ""
        pid_match = re.search(r"(\d+)", post_id_str)
        pid = int(pid_match.group(1)) if pid_match else 0

        # Author
        author_el = post_el.select_one(
            ".author, .username, td.userinfo .author, .postername, a.author"
        )
        author = _text(author_el)

        # Author ID (from profile link)
        author_link = post_el.select_one("a[href*='userid='], a[href*='member.php']")
        aid_match = re.search(r"userid=(\d+)", _attr(author_link, "href"))
        author_id = int(aid_match.group(1)) if aid_match else 0

        # Post date
        date_el = post_el.select_one(".postdate, .date, td.postdate")
        post_date = _text(date_el)

        # Post content — strip quotes for brevity but note they exist
        content_el = post_el.select_one(".postbody, .post-body, td.postbody")
        if content_el:
            # Remove quote blocks to show just the main content, add a note
            for quote in content_el.select(".bbc-block, blockquote, .quote"):
                quote_author_el = quote.select_one(".author, cite")
                qa = _text(quote_author_el) if quote_author_el else "someone"
                quote.replace_with(f"[quote from {qa}]")
            content = content_el.get_text(" ", strip=True)
        else:
            content = ""

        # Avatar
        avatar_el = post_el.select_one("img.avatar, .useravatar img, td.userinfo img")
        avatar_url = _attr(avatar_el, "src") if avatar_el else ""

        if author or content:  # Skip empty rows
            posts.append(
                {
                    "id": pid,
                    "author": author,
                    "author_id": author_id,
                    "date": post_date,
                    "content": content,
                    "avatar_url": avatar_url,
                }
            )

    if not posts:
        return (
            f"No posts found in thread {params.thread_id} page {effective_page}. "
            "Make sure you're logged in (sa_login) and the thread ID is correct."
        )

    if params.response_format == "json":
        return json.dumps(
            {
                "thread_id": params.thread_id,
                "thread_title": thread_title,
                "page": effective_page,
                "total_pages": total_pages,
                "posts": posts,
            },
            indent=2,
        )

    lines = [f"# {thread_title} (page {effective_page} of {total_pages})\n"]
    for p in posts:
        lines.append("---")
        pid_str = f" | Post #{p['id']}" if p["id"] else ""
        date_str = f" | {p['date']}" if p["date"] else ""
        lines.append(f"**{p['author'] or 'unknown'}**{pid_str}{date_str}")
        lines.append("")
        lines.append(p["content"] or "(empty post)")
        lines.append("")
    lines.append("---")
    return "\n".join(lines)


@mcp.tool(
    name="sa_search",
    annotations={
        "title": "Search SA Forums",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_search(params: SearchInput) -> str:
    """Search the Something Awful Forums for threads and posts.

    Performs a full-text search across all forums (or a specific forum).
    Returns matching thread/post results with links and excerpts.

    Args:
        params (SearchInput): Input parameters containing:
            - query (str): Search terms (required)
            - forum_id (Optional[int]): Restrict to a specific forum
            - user (Optional[str]): Restrict to posts by a specific username
            - page (int): Page of results (default: 1)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        str: Search results in the requested format.
    """
    q = params.query
    if params.user:
        q = f'{q} username:"{params.user}"'

    post_data: Dict[str, Any] = {
        "action": "query",
        "q": q,
    }
    if params.forum_id is not None:
        post_data["forums[]"] = str(params.forum_id)

    try:
        resp = await _session.post(f"{BASE_URL}/query.php", data=post_data)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    # SA may redirect to a results URL with qid, or return an intermediate page.
    qid_match = re.search(r"qid=(\d+)", str(resp.url))
    if not qid_match:
        soup = _soup(resp.text)
        qid_link = soup.select_one("a[href*='qid=']")
        if qid_link:
            qid_match = re.search(r"qid=(\d+)", _attr(qid_link, "href"))

    if not qid_match:
        page_text = resp.text.lower()
        if "search the forums" in page_text and "example searches" in page_text:
            return (
                f"Error: SA returned the search form instead of results for '{params.query}'. "
                "Try a simpler query or constrain it to a forum."
            )
        if "no results" in page_text or "0 results" in page_text:
            return f"No results found for '{params.query}'. Try different search terms."
        return (
            "Error: Could not initiate search — SA may have rejected the query, "
            "returned the form again, or be rate limiting."
        )

    qid = qid_match.group(1)

    results_url = f"{BASE_URL}/query.php?action=results&qid={qid}&page={params.page}"
    try:
        resp = await _session.get(results_url)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    soup = _soup(resp.text)

    total_pages = 1
    result_count = 0
    for tag in soup.find_all(string=re.compile(r"Showing results", re.I)):
        count_text = tag.strip()
        count_match = re.search(r"of\s+([\d,]+)\s+results?", count_text, re.I)
        per_match = re.search(r"results\s+(\d+)\s+to\s+(\d+)", count_text, re.I)
        if count_match:
            result_count = int(count_match.group(1).replace(",", ""))
        if per_match and result_count:
            per_page = int(per_match.group(2)) - int(per_match.group(1)) + 1
            if per_page > 0:
                import math
                total_pages = math.ceil(result_count / per_page)
        break

    results: List[Dict[str, Any]] = []

    thread_links = soup.select("a[href*='showthread.php']")
    for thread_link in thread_links:
        href = _attr(thread_link, "href")
        pid_match = re.search(r"postid=(\d+)", href)
        tid_match = re.search(r"threadid=(\d+)", href)
        pid = int(pid_match.group(1)) if pid_match else 0
        tid = int(tid_match.group(1)) if tid_match else 0
        thread_title = _text(thread_link)

        parent = thread_link.parent
        author_link = None
        forum_link = None
        for _ in range(5):
            if parent is None:
                break
            author_link = parent.find("a", href=re.compile(r"member\.php"))
            forum_link = parent.find("a", href=re.compile(r"forumdisplay\.php"))
            if author_link or forum_link:
                break
            parent = parent.parent

        author = _text(author_link) if author_link else ""
        forum_name = _text(forum_link) if forum_link else ""

        parent_text = parent.get_text(" ", strip=True) if parent else ""
        date = ""
        date_match = re.search(r"\bat\s+(\w{3}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2})", parent_text)
        if date_match:
            date = date_match.group(1)

        excerpt = ""
        if date_match and parent:
            after_date = parent_text[date_match.end():].strip()
            excerpt = after_date[:300]

        results.append(
            {
                "thread_id": tid,
                "thread_title": thread_title,
                "post_id": pid,
                "forum": forum_name,
                "author": author,
                "date": date,
                "excerpt": excerpt,
            }
        )

    if not results:
        return f"No results found for '{params.query}'. Try different search terms or check your login status."

    if params.response_format == "json":
        return json.dumps(
            {
                "query": params.query,
                "page": params.page,
                "total_pages": total_pages,
                "result_count": result_count,
                "results": results,
            },
            indent=2,
        )

    lines = [f'# Search Results for "{params.query}" (page {params.page} of {total_pages})\n']
    if result_count:
        lines.append(f"*{result_count} total results*\n")
    for r in results:
        lines.append(f"## {r['thread_title'] or 'Unknown Thread'}")
        meta_parts = []
        if r["forum"]:
            meta_parts.append(f"**Forum**: {r['forum']}")
        if r["author"]:
            meta_parts.append(f"**Posted by**: {r['author']}")
        if r["date"]:
            meta_parts.append(f"**Date**: {r['date']}")
        if r["post_id"]:
            meta_parts.append(f"**Post ID**: {r['post_id']}")
        if meta_parts:
            lines.append("- " + " | ".join(meta_parts))
        if r["excerpt"]:
            lines.append(f"\n> {r['excerpt']}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(
    name="sa_get_user",
    annotations={
        "title": "Get SA User Profile",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_get_user(params: GetUserInput) -> str:
    """Look up a Something Awful user's profile.

    Fetches profile information for a SA user by username or user ID.
    Returns registration date, post count, avatar, bio, and other profile data.

    Args:
        params (GetUserInput): Input parameters containing:
            - username (Optional[str]): SA username to look up
            - user_id (Optional[int]): SA user ID to look up
            - response_format (str): 'markdown' (default) or 'json'
            At least one of username or user_id must be provided.

    Returns:
        str: User profile in the requested format.

        Markdown format:
            # Profile: username (ID: 123)
            - **Joined**: Jan 1, 2004
            - **Posts**: 5,000
            - **Title**: Custom Title
            - **Bio**: ...

        JSON format:
        {
          "id": 123,
          "username": "username",
          "joined": "Jan 1, 2004",
          "posts": 5000,
          "title": "Custom Title",
          "location": "...",
          "bio": "...",
          "avatar_url": "https://..."
        }

        Error response: "Error: <message>"

    Examples:
        - "Look up user Lowtax" → username="Lowtax"
        - "Get profile for user ID 12345" → user_id=12345
    """
    if not params.username and not params.user_id:
        return "Error: Provide either 'username' or 'user_id'."

    query: Dict[str, Any] = {"action": "getinfo"}
    if params.user_id:
        query["userid"] = params.user_id
    else:
        query["username"] = params.username

    url = f"{BASE_URL}/member.php"
    try:
        resp = await _session.get(url, params=query)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    soup = _soup(resp.text)

    # Extract user info
    # User ID from page (e.g. in links or hidden fields)
    uid = params.user_id or 0
    uid_match = re.search(r"userid=(\d+)", resp.text)
    if uid_match:
        uid = int(uid_match.group(1))

    # Username from page title or heading
    username_el = soup.select_one("h1, .username, .profile-username, title")
    username = _text(username_el).replace(" - Something Awful Forums", "").strip()
    if params.username and not username:
        username = params.username

    # Profile fields — SA uses a definition list or table for profile info
    profile: Dict[str, str] = {}
    for row in soup.select("dl.profileinfo dt, table.profileinfo tr, .profile-row"):
        label_el = row.select_one("dt, th, .label")
        value_el = row.select_one("dd, td:last-child, .value")
        if label_el and value_el:
            label = _text(label_el).lower().rstrip(":").strip()
            value = _text(value_el)
            profile[label] = value

    # Common fields with fallback selectors
    joined = profile.get("joined", profile.get("member since", profile.get("registered", "")))
    posts = profile.get("posts", profile.get("post count", ""))
    title = profile.get("title", profile.get("custom title", ""))
    location = profile.get("location", profile.get("hometown", ""))
    bio = profile.get("biography", profile.get("bio", profile.get("about", "")))

    # Avatar
    avatar_el = soup.select_one(".avatar img, .profile-avatar img, img.avatar")
    avatar_url = _attr(avatar_el, "src") if avatar_el else ""

    # Posts count as int
    posts_int = 0
    if posts:
        try:
            posts_int = int(posts.replace(",", "").strip())
        except ValueError:
            pass

    user_data = {
        "id": uid,
        "username": username,
        "joined": joined,
        "posts": posts_int if posts_int else posts,
        "title": title,
        "location": location,
        "bio": bio,
        "avatar_url": avatar_url,
    }

    if params.response_format == "json":
        return json.dumps(user_data, indent=2)

    lines = [f"# Profile: {username}" + (f" (ID: {uid})" if uid else "") + "\n"]
    if joined:
        lines.append(f"- **Joined**: {joined}")
    if posts:
        lines.append(f"- **Posts**: {posts}")
    if title:
        lines.append(f"- **Title**: {title}")
    if location:
        lines.append(f"- **Location**: {location}")
    if bio:
        lines.append(f"- **Bio**: {bio}")
    if avatar_url:
        lines.append(f"- **Avatar**: {avatar_url}")
    if not any([joined, posts, title, location, bio]):
        lines.append("*(No additional profile info found — user may have a private profile)*")
    return "\n".join(lines)


@mcp.tool(
    name="sa_list_pms",
    annotations={
        "title": "List SA Private Messages",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_list_pms(params: ListPMsInput) -> str:
    """List private messages in a Something Awful inbox folder.

    Fetches a page of private messages from the specified folder.
    Use pm_id values with sa_get_pm to read individual messages.

    Must be logged in (sa_login) to use this tool.

    Args:
        params (ListPMsInput): Input parameters containing:
            - folder_id (int): 0 = Inbox, 1 = Sent, others for custom folders (default: 0)
            - page (int): Page of messages to fetch (default: 1)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        str: PM list in the requested format.

        Markdown format:
            # Private Messages — Inbox (page 1 of N)
            ## Subject Line (PM ID: 789)
            - **From**: sender | **Date**: Jan 1, 2024 | **Status**: unread

        JSON format:
        {
          "folder": "Inbox",
          "folder_id": 0,
          "page": 1,
          "total_pages": 2,
          "messages": [
            {
              "id": 789,
              "subject": "Subject Line",
              "sender": "username",
              "date": "Jan 1, 2024",
              "read": false
            }
          ]
        }

        Error response: "Error: <message>" (often means not logged in)

    Examples:
        - "Check my SA inbox" → default params
        - "Show sent messages" → folder_id=1
    """
    url = f"{BASE_URL}/private.php"
    query: Dict[str, Any] = {
        "action": "show",
        "folderid": params.folder_id,
        "pagenumber": params.page,
    }
    try:
        resp = await _session.get(url, params=query)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    # Check for redirect to login page
    if "loginform" in str(resp.url) or "account.php" in str(resp.url):
        return _require_login_msg()

    soup = _soup(resp.text)
    total_pages = _extract_page_count(soup)

    # Folder name
    folder_names = {0: "Inbox", 1: "Sent"}
    folder_name = folder_names.get(params.folder_id, f"Folder {params.folder_id}")
    folder_el = soup.select_one(".selected-folder, .folder-name, option[selected]")
    if folder_el:
        folder_name = _text(folder_el) or folder_name

    messages: List[Dict[str, Any]] = []

    for row in soup.select("table#pm tr.pm, tr.privatemessage, .message-row"):
        # PM ID from link
        link = row.select_one("a[href*='privatemessageid=']")
        if not link:
            continue
        href = _attr(link, "href")
        pm_match = re.search(r"privatemessageid=(\d+)", href)
        pm_id = int(pm_match.group(1)) if pm_match else 0

        subject = _text(link)

        # Sender/recipient
        sender_el = row.select_one(".sender, .from, td.sender, a[href*='userid']")
        sender = _text(sender_el)

        # Date
        date_el = row.select_one(".date, td.date, .pmdate")
        date = _text(date_el)

        # Read status
        is_unread = bool(row.select_one(".unread, .new")) or "unread" in row.get("class", [])

        messages.append(
            {
                "id": pm_id,
                "subject": subject,
                "sender": sender,
                "date": date,
                "read": not is_unread,
            }
        )

    if not messages:
        return (
            f"No messages found in {folder_name}. "
            "Make sure you're logged in (sa_login) and have messages in this folder."
        )

    if params.response_format == "json":
        return json.dumps(
            {
                "folder": folder_name,
                "folder_id": params.folder_id,
                "page": params.page,
                "total_pages": total_pages,
                "messages": messages,
            },
            indent=2,
        )

    lines = [f"# Private Messages — {folder_name} (page {params.page} of {total_pages})\n"]
    for m in messages:
        status = "🔵 unread" if not m["read"] else "read"
        lines.append(f"## {m['subject'] or '(no subject)'} (PM ID: {m['id']})")
        meta_parts = []
        if m["sender"]:
            meta_parts.append(f"**From**: {m['sender']}")
        if m["date"]:
            meta_parts.append(f"**Date**: {m['date']}")
        meta_parts.append(f"**Status**: {status}")
        lines.append("- " + " | ".join(meta_parts))
        lines.append("")
    return "\n".join(lines)


@mcp.tool(
    name="sa_get_pm",
    annotations={
        "title": "Read a SA Private Message",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_get_pm(params: GetPMInput) -> str:
    """Read the full content of a Something Awful private message.

    Fetches a single private message by ID. Must be logged in (sa_login).
    Use sa_list_pms to find PM IDs.

    Args:
        params (GetPMInput): Input parameters containing:
            - pm_id (int): Private message ID from sa_list_pms (required)
            - response_format (str): 'markdown' (default) or 'json'

    Returns:
        str: Private message content in the requested format.

        Markdown format:
            # PM: Subject Line (ID: 789)
            - **From**: sender | **To**: recipient | **Date**: Jan 1, 2024
            ---
            Message body text...

        JSON format:
        {
          "id": 789,
          "subject": "Subject Line",
          "sender": "username",
          "recipient": "yourname",
          "date": "Jan 1, 2024",
          "body": "Message body..."
        }

        Error response: "Error: <message>"

    Examples:
        - "Read PM 789" → pm_id=789
        - "Show me what message 12345 says" → pm_id=12345
    """
    url = f"{BASE_URL}/private.php"
    query: Dict[str, Any] = {
        "action": "show",
        "privatemessageid": params.pm_id,
    }
    try:
        resp = await _session.get(url, params=query)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    if "loginform" in str(resp.url) or "account.php" in str(resp.url):
        return _require_login_msg()

    soup = _soup(resp.text)

    # Subject
    subject_el = soup.select_one("h1, .subject, .pm-subject, title")
    subject = _text(subject_el).replace(" - Something Awful Forums", "").strip()

    # Sender / recipient
    sender_el = soup.select_one(".sender, .from, a[href*='userid']")
    sender = _text(sender_el)

    to_el = soup.select_one(".to, .recipient")
    recipient = _text(to_el)

    # Date
    date_el = soup.select_one(".date, .pmdate")
    date = _text(date_el)

    # Body
    body_el = soup.select_one(".postbody, .pm-body, .message-body, .body")
    if body_el:
        # Strip quotes with notation
        for quote in body_el.select(".bbc-block, blockquote, .quote"):
            qa_el = quote.select_one(".author, cite")
            qa = _text(qa_el) if qa_el else "someone"
            quote.replace_with(f"\n[quote from {qa}]\n")
        body = body_el.get_text("\n", strip=True)
    else:
        body = ""

    pm_data = {
        "id": params.pm_id,
        "subject": subject,
        "sender": sender,
        "recipient": recipient,
        "date": date,
        "body": body,
    }

    if params.response_format == "json":
        return json.dumps(pm_data, indent=2)

    lines = [f"# PM: {subject or '(no subject)'} (ID: {params.pm_id})\n"]
    meta: List[str] = []
    if sender:
        meta.append(f"**From**: {sender}")
    if recipient:
        meta.append(f"**To**: {recipient}")
    if date:
        meta.append(f"**Date**: {date}")
    if meta:
        lines.append("- " + " | ".join(meta))
    lines.append("\n---\n")
    lines.append(body or "*(empty message)*")
    return "\n".join(lines)
@mcp.tool(
    name="sa_list_usercp_threads",
    annotations={
        "title": "List Threads on User Control Panel",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def sa_list_usercp_threads(params: ListUserCPThreadsInput) -> str:
    """List thread links shown on the SA user control panel page.

    Fetches https://forums.somethingawful.com/usercp.php and extracts any
    thread entries visible on the page, which commonly includes subscribed
    threads / watched threads / thread-related links.
    """
    url = f"{BASE_URL}/usercp.php"
    try:
        resp = await _session.get(url)
        resp.raise_for_status()
    except Exception as e:
        return _handle_error(e)

    if "loginform" in str(resp.url) or "account.php" in str(resp.url):
        return _require_login_msg()

    soup = _soup(resp.text)

    threads: List[Dict[str, Any]] = []
    seen: set[int] = set()

    # Look for thread links anywhere on the page, then capture nearby context.
    for link in soup.select("a[href*='showthread.php']"):
        href = _attr(link, "href")
        tid_match = re.search(r"threadid=(\d+)", href)
        if not tid_match:
            continue
        thread_id = int(tid_match.group(1))
        if thread_id in seen:
            continue
        seen.add(thread_id)

        thread_title = _text(link)
        forum_name = ""
        context = ""
        unread_url = ""
        unread_count = 0
        unread_page = 0
        first_unread_post_id = 0
        last_page_url = ""
        last_page_num = 0
        last_post_url = ""

        row = link.find_parent("tr")
        title = _extract_thread_title_from_row(row) if row is not None else ""
        if not title:
            title = _clean_thread_title(_text(link))

        if row is not None:
            unread_url = _extract_unread_link_from_row(row)
            unread_count = _extract_unread_count_from_row(row)

            # Prefer a direct last-post jump if present
            lastpost_link = row.select_one("a[href*='goto=lastpost']")
            if lastpost_link:
                last_post_url = f"{BASE_URL}/{_attr(lastpost_link, 'href').lstrip('/')}"

            # Otherwise fall back to the highest page number link
            page_links = row.select("a[href*='pagenumber=']")
            for a in page_links:
                page_href = _attr(a, "href")
                page_match = re.search(r"pagenumber=(\d+)", page_href)
                if page_match:
                    page_num = int(page_match.group(1))
                    if page_num > last_page_num:
                        last_page_num = page_num
                        last_page_url = f"{BASE_URL}/{page_href.lstrip('/')}"

            if not last_post_url:
                last_post_url = last_page_url

        if unread_url:
            try:
                unread_resp = await _session.get(unread_url)
                unread_resp.raise_for_status()
                final_url = str(unread_resp.url)

                page_match = re.search(r"pagenumber=(\d+)", final_url)
                if page_match:
                    unread_page = int(page_match.group(1))

                post_match = re.search(r"postid=(\d+)", final_url)
                if post_match:
                    first_unread_post_id = int(post_match.group(1))
            except Exception:
                pass

        parent = link.parent
        for _ in range(4):
            if parent is None:
                break
            forum_link = parent.find("a", href=re.compile(r"forumdisplay\.php"))
            if forum_link:
                forum_name = _text(forum_link)
                break
            parent = parent.parent

        container = link.parent
        for _ in range(2):
            if container is None:
                break
            context_text = _text(container)
            if context_text and len(context_text) > len(thread_title):
                context = context_text
                break
            container = container.parent

        threads.append(
            {
                "thread_id": thread_id,
                "thread_title": title,
                "forum": forum_name,
                "url": f"{BASE_URL}/{href.lstrip('/')}",
                "last_page_url": last_page_url,
                "last_page_num": last_page_num,
                "last_post_url": last_post_url,
                "context": context,
                "unread_count": unread_count,
                "unread_url": unread_url,
                "unread_page": unread_page,
                "first_unread_post_id": first_unread_post_id,
            }
        )

    if not threads:
        return "No thread links found on usercp.php. Make sure you're logged in (sa_login) and that the page contains subscribed/watch threads."

    if params.response_format == "json":
        return json.dumps({"page": "usercp", "threads": threads}, indent=2)

    lines = ["# User CP Threads\n"]
    for t in threads:
        title = t["thread_title"]
        if t["unread_count"] > 0:
            title += f" 🔵 ({t['unread_count']} unread)"
        lines.append(f"## {title} (Thread ID: {t['thread_id']})")
        meta_parts = []
        if t["forum"]:
            meta_parts.append(f"**Forum**: {t['forum']}")
        meta_parts.append(f"**Link**: {t['url']}")
        if t["last_page_num"]:
            meta_parts.append(f"**Last page**: {t['last_page_num']}")
        if t["last_post_url"]:
            meta_parts.append(f"**Last post**: {t['last_post_url']}")
        if t["unread_url"]:
            meta_parts.append(f"**Unread link**: {t['unread_url']}")
        if t["unread_page"]:
            meta_parts.append(f"**Unread page**: {t['unread_page']}")
        if t["first_unread_post_id"]:
            meta_parts.append(f"**First unread post**: {t['first_unread_post_id']}")
        lines.append("- " + " | ".join(meta_parts))
        if t["context"]:
            lines.append(f"> {t['context']}")
        lines.append("")
    return "\n".join(lines)
############### Health Check ##################
from starlette.routing import Route
from starlette.responses import JSONResponse

async def health(request):
    return JSONResponse({"status": "ok", "logged_in": _session.logged_in})

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "logged_in": _session.logged_in})
################################
########### main##############
if __name__ == "__main__":
    import os
    import uvicorn
    from starlette.responses import JSONResponse

    # Add health route directly to FastMCP
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
