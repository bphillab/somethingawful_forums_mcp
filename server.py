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
from tools.user import register_tools as register_user_tools

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