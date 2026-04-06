from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from helpers import (
    _attr,
    _clean_thread_title,
    _extract_page_count,
    _extract_thread_title_from_row,
    _handle_error,
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
            page_match = re.search(r"pagenumber=(\d+)", final_url)
            effective_page = int(page_match.group(1)) if page_match else 1
            tid_match = re.search(r"threadid=(\d+)", final_url)
            effective_thread_id = int(tid_match.group(1)) if tid_match else params.thread_id
        elif params.last_page:
            effective_page = total_pages
        else:
            effective_page = params.page

        posts: List[Dict[str, Any]] = []

        post_tables = soup.select("table.post, div.post, .postbody, tr.post")
        if not post_tables:
            post_tables = soup.select("[id^='post']")

        for post_el in post_tables:
            post_id_str = post_el.get("id", "") or ""
            pid_match = re.search(r"(\d+)", post_id_str)
            pid = int(pid_match.group(1)) if pid_match else 0

            author_el = post_el.select_one(
                ".author, .username, td.userinfo .author, .postername, a.author"
            )
            author = _text(author_el)

            author_link = post_el.select_one("a[href*='userid='], a[href*='member.php']")
            aid_match = re.search(r"userid=(\d+)", _attr(author_link, "href"))
            author_id = int(aid_match.group(1)) if aid_match else 0

            date_el = post_el.select_one(".postdate, .date, td.postdate")
            post_date = _text(date_el)

            content_el = post_el.select_one(".postbody, .post-body, td.postbody")
            if content_el:
                for quote in content_el.select(".bbc-block, blockquote, .quote"):
                    quote_author_el = quote.select_one(".author, cite")
                    qa = _text(quote_author_el) if quote_author_el else "someone"
                    quote.replace_with(f"[quote from {qa}]")
                content = content_el.get_text(" ", strip=True)
            else:
                content = ""

            avatar_el = post_el.select_one("img.avatar, .useravatar img, td.userinfo img")
            avatar_url = _attr(avatar_el, "src") if avatar_el else ""

            if author or content:
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

        if not params.goto_post_id:
            effective_thread_id = params.thread_id

        if not posts:
            return (
                f"No posts found in thread {effective_thread_id} page {effective_page}. "
                "Make sure you're logged in (sa_login) and the thread ID is correct."
            )

        if params.response_format == "json":
            return json.dumps(
                {
                    "thread_id": effective_thread_id,
                    "thread_title": thread_title,
                    "page": effective_page,
                    "total_pages": total_pages,
                    "posts": posts,
                },
                indent=2,
            )

        lines = [f"# {thread_title} (Thread ID: {effective_thread_id} | page {effective_page} of {total_pages})\n"]
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