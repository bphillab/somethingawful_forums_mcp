from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from helpers import (
    _attr,
    _clean_thread_title,
    _extract_id,
    _extract_thread_title_from_row,
    _extract_unread_count_from_row,
    _extract_unread_link_from_row,
    _handle_error,
    _require_login_msg,
    _soup,
    _text,
    _tool_annotations,
)
from constants import BASE_URL
from models import ListUserCPThreadsInput
from session import SASession


def register_tools(mcp: FastMCP, session: SASession) -> None:
    @mcp.tool(
        name="sa_list_usercp_threads",
        annotations=_tool_annotations("List Threads on User Control Panel"),
    )
    async def sa_list_usercp_threads(params: ListUserCPThreadsInput) -> str:
        """List thread links shown on the SA user control panel page.

        Each thread with unread posts includes an unread_count field.
        When reading a thread's new posts, use sa_get_thread with last_page=True
        and last_n_posts=<unread_count> to fetch only the unread posts."""
        url = f"{BASE_URL}/usercp.php"
        try:
            resp = await session.get(url)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        if "loginform" in str(resp.url) or "account.php" in str(resp.url):
            return _require_login_msg()

        soup = _soup(resp.text)

        threads: List[Dict[str, Any]] = []
        seen: set[int] = set()

        for link in soup.select("a[href*='showthread.php']"):
            href = _attr(link, "href")
            thread_id = _extract_id(href, "threadid")
            if not thread_id:
                continue
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
            replies = 0
            last_post_date = ""
            last_poster = ""

            row = link.find_parent("tr")
            title = _extract_thread_title_from_row(row) if row is not None else ""
            if not title:
                title = _clean_thread_title(_text(link))

            if row is not None:
                unread_url = _extract_unread_link_from_row(row)
                unread_count = _extract_unread_count_from_row(row)

                reply_el = row.select_one(".replies, td.replies, .replycount")
                replies_text = _text(reply_el).replace(",", "")
                if replies_text.isdigit():
                    replies = int(replies_text)

                lastpost_el = row.select_one(".lastpost, td.lastpost, .lastpostinfo")
                if lastpost_el:
                    date_el = lastpost_el.select_one(".date")
                    poster_el = lastpost_el.select_one("a")
                    last_post_date = _text(date_el) if date_el else ""
                    last_poster = _text(poster_el) if poster_el else ""

                lastpost_link = row.select_one("a[href*='goto=lastpost']")
                if lastpost_link:
                    last_post_url = f"{BASE_URL}/{_attr(lastpost_link, 'href').lstrip('/')}"

                page_links = row.select("a[href*='pagenumber=']")
                for a in page_links:
                    page_href = _attr(a, "href")
                    page_num = _extract_id(page_href, "pagenumber")
                    if page_num > last_page_num:
                            last_page_num = page_num
                            last_page_url = f"{BASE_URL}/{page_href.lstrip('/')}"

                if not last_post_url:
                    last_post_url = last_page_url

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
                    "replies": replies,
                    "last_post_date": last_post_date,
                    "last_poster": last_poster,
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
            return (
                "No thread links found on usercp.php. Make sure you're logged in "
                "(sa_login) and that the page contains subscribed/watch threads."
            )

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
            if t["replies"]:
                meta_parts.append(f"**Replies**: {t['replies']}")
            if t["last_poster"]:
                meta_parts.append(f"**Last post**: {t['last_post_date']} by {t['last_poster']}")
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