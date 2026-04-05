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
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field

from helpers import (
    _attr,
    _clean_thread_title,
    _extract_last_page_url_from_row,
    _extract_page_count,
    _extract_thread_title_from_row,
    _extract_unread_count_from_row,
    _extract_unread_link_from_row,
    _handle_error,
    _require_login_msg,
    _soup,
    _text,
)

from models import (
    GetPMInput,
    GetThreadInput,
    GetUserInput, LoginInput, ListForumsInput, ListThreadsInput, SearchInput, ListPMsInput, ListUserCPThreadsInput,
)

from tools.auth import register_tools as register_auth_tools
from tools.forums import register_tools as register_forums_tools
from tools.threads import register_tools as register_threads_tools
from tools.search import register_tools as register_search_tools
from tools.users import register_tools as register_user_tools
from tools.pms import register_tools as register_pms_tools
from tools.usercp import register_tools as register_usercp_tools

from session import SASession


# ─────────────────────────── Constants ────────────────────────────────────────

BASE_URL = "https://forums.somethingawful.com"
DEFAULT_PER_PAGE = 40

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