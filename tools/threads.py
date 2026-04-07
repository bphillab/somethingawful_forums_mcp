from __future__ import annotations

import json
import re
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from helpers import (
    _attr,
    _extract_page_count,
    _extract_thread_title_from_row,
    _handle_error,
    _page_from_redirect,
    _parse_posts,
    _soup,
    _text,
)
from models import GetThreadInput, ListThreadsInput
from session import SASession

BASE_URL = "https://forums.somethingawful.com"
DEFAULT_PER_PAGE = 40


def register_tools(mcp: FastMCP, session: SASession) -> None:
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
        """List threads in a specific Something Awful forum."""
        url = (
            f"{BASE_URL}/forumdisplay.php"
            f"?forumid={params.forum_id}&perpage={DEFAULT_PER_PAGE}&pagenumber={params.page}"
        )
        try:
            resp = await session.get(url)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        soup = _soup(resp.text)

        forum_name_el = soup.select_one("h1, .forum-name, title")
        forum_name = _text(forum_name_el).replace(" - Something Awful Forums", "").strip()

        total_pages = _extract_page_count(soup)

        threads: List[Dict[str, Any]] = []

        thread_rows = soup.select(
            "table#forum tr.thread, "
            "tr.thread, "
            "tr[id^='thread'], "
            ".threadlist tr:not(:first-child)"
        )

        for row in thread_rows:
            link = row.select_one("a.thread_title, a[href*='showthread.php']")
            if not link:
                continue
            href = _attr(link, "href")
            tid_match = re.search(r"threadid=(\d+)", href)
            if not tid_match:
                continue

            tid = int(tid_match.group(1))
            title = _extract_thread_title_from_row(row)

            author_el = row.select_one(".author, td.author, .threadauthor")
            author = _text(author_el)

            reply_el = row.select_one(".replies, td.replies, .replycount")
            replies_text = _text(reply_el).replace(",", "")
            try:
                replies = int(replies_text)
            except ValueError:
                replies = 0

            view_el = row.select_one(".views, td.views")
            views_text = _text(view_el).replace(",", "")
            try:
                views = int(views_text)
            except ValueError:
                views = 0

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

        lines = [
            f"# Threads in {forum_name or f'Forum {params.forum_id}'} "
            f"(page {params.page} of {total_pages})\n"
        ]
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
        """Read posts from a Something Awful thread."""
        if params.goto_post_id:
            url = (
                f"{BASE_URL}/showthread.php"
                f"?goto=post&noseen=1&postid={params.goto_post_id}"
            )
        elif not params.thread_id:
            return "thread_id is required when goto_post_id is not set."
        elif params.goto_newpost:
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
            resp = await session.get(url)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        soup = _soup(resp.text)

        title_el = soup.select_one("title, h1, .thread-title")
        thread_title = _text(title_el).replace(" - Something Awful Forums", "").strip()

        total_pages = _extract_page_count(soup)

        if params.goto_post_id:
            final_url = str(resp.url)
            effective_page = _page_from_redirect(final_url, soup) or "?"
            tid_match = re.search(r"threadid=(\d+)", final_url)
            effective_thread_id = int(tid_match.group(1)) if tid_match else params.thread_id
        elif params.goto_newpost:
            final_url = str(resp.url)
            effective_page = _page_from_redirect(final_url, soup) or "?"
        elif params.last_page:
            effective_page = total_pages
        else:
            effective_page = params.page

        posts = _parse_posts(soup)

        if not params.goto_post_id:
            effective_thread_id = params.thread_id

        first_unread_post_id = 0
        if params.goto_newpost:
            first_unseen = soup.select_one("table.post.seen0, tr.post.seen0")
            if first_unseen:
                unseen_id_match = re.search(r"(\d+)", first_unseen.get("id", "") or "")
                if unseen_id_match:
                    first_unread_post_id = int(unseen_id_match.group(1))

        if params.since_post_id:
            posts = [p for p in posts if p["id"] > params.since_post_id]

        unread_fetched = len(posts) if params.since_post_id else None

        if not posts:
            return (
                f"No posts found in thread {effective_thread_id} page {effective_page}. "
                "Make sure you're logged in (sa_login) and the thread ID is correct."
            )

        if params.response_format == "json":
            result: Dict[str, Any] = {
                "thread_id": effective_thread_id,
                "thread_title": thread_title,
                "page": effective_page,
                "total_pages": total_pages,
                "posts": posts,
            }
            if first_unread_post_id:
                result["first_unread_post_id"] = first_unread_post_id
            if unread_fetched is not None:
                result["unread_posts_fetched"] = unread_fetched
            return json.dumps(result, indent=2)

        header = f"# {thread_title} (Thread ID: {effective_thread_id} | page {effective_page} of {total_pages})"
        if first_unread_post_id:
            header += f" | First unread post: #{first_unread_post_id}"
        if unread_fetched is not None:
            header += f" | {unread_fetched} unread posts fetched"
        lines = [header + "\n"]
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