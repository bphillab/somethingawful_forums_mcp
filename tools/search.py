from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from constants import BASE_URL
from helpers import _attr, _extract_page_count, _handle_error, _soup, _text
from models import SearchInput
from session import SASession


def register_tools(mcp: FastMCP, session: SASession) -> None:
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

        Requires an active login session. If search returns errors or unexpected results,
        call sa_login first before retrying. Do not assume rate limiting until login has
        been confirmed."""
        q = params.query
        if params.title:
            q = f'{q} intitle:"{params.title}"'
        if params.user:
            q = f'{q} username:"{params.user}"'
        if params.quoting:
            q = f'{q} quoting:"{params.quoting}"'
        if params.since:
            q = f'{q} since:"{params.since}"'
        if params.before:
            q = f'{q} before:"{params.before}"'
        if params.userid:
            q = f"{q} userid:{params.userid}"
        if params.threadid:
            q = f"{q} threadid:{params.threadid}"

        post_data: Dict[str, Any] = {
            "action": "query",
            "q": q,
        }
        if params.forum_id is not None:
            post_data["forums[]"] = str(params.forum_id)

        try:
            resp = await session.post(f"{BASE_URL}/query.php", data=post_data)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        qid_match = re.search(r"qid=(\d+)", str(resp.url))
        if not qid_match:
            soup = _soup(resp.text)
            qid_link = soup.select_one("a[href*='qid=']")
            if qid_link:
                qid_match = re.search(r"qid=(\d+)", _attr(qid_link, "href"))

        if not qid_match:
            page_text = resp.text.lower()
            if "search the forums" in page_text and "example searches" in page_text:
                if "log in" in page_text or "login" in page_text or "register" in page_text:
                    return (
                        f"Error: SA returned the search form instead of results for '{params.query}'. "
                        "This usually means you are not logged in — try calling sa_login first."
                    )
                return (
                    f"Error: SA returned the search form instead of results for '{params.query}'. "
                    "Try a simpler query or constrain it to a forum. If the problem persists, try sa_login."
                )
            if "no results" in page_text or "0 results" in page_text:
                return f"No results found for '{params.query}'. Try different search terms."
            return (
                "Error: Could not initiate search — SA may have rejected the query, "
                "returned the form again, or be rate limiting. If you haven't logged in, try sa_login first."
            )

        qid = qid_match.group(1)

        results_url = f"{BASE_URL}/query.php?action=results&qid={qid}&page={params.page}"
        try:
            resp = await session.get(results_url)
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
                    total_pages = math.ceil(result_count / per_page)
            break

        async def resolve_thread_id(pid: int) -> int:
            try:
                r = await session.get(
                    f"{BASE_URL}/showthread.php?goto=post&postid={pid}"
                )
                tid_m = re.search(r"threadid=(\d+)", str(r.url))
                return int(tid_m.group(1)) if tid_m else 0
            except Exception:
                return 0

        thread_links = soup.select("a[href*='showthread.php']")
        raw: List[Dict[str, Any]] = []
        for thread_link in thread_links:
            href = _attr(thread_link, "href")
            pid_match = re.search(r"postid=(\d+)", href)
            tid_match = re.search(r"threadid=(\d+)", href)
            post_id = int(pid_match.group(1)) if pid_match else 0
            thread_id = int(tid_match.group(1)) if tid_match else 0
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

            raw.append(
                {
                    "thread_id": thread_id,
                    "thread_title": thread_title,
                    "post_id": post_id,
                    "forum": forum_name,
                    "author": author,
                    "date": date,
                    "excerpt": excerpt,
                }
            )

        # Resolve missing thread IDs concurrently via post redirect (opt-in)
        if params.resolve_thread_ids:
            missing = [r for r in raw if r["thread_id"] == 0 and r["post_id"]]
            if missing:
                resolved = await asyncio.gather(
                    *[resolve_thread_id(r["post_id"]) for r in missing]
                )
                for entry, tid in zip(missing, resolved):
                    entry["thread_id"] = tid

        results = raw

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