from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from constants import BASE_URL
from helpers import _attr, _handle_error, _soup, _text
from models import ListForumsInput
from session import SASession


def register_tools(mcp: FastMCP, session: SASession) -> None:
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
        """
        try:
            resp = await session.get(f"{BASE_URL}/index.php")
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

            subforums: List[Dict[str, Any]] = []
            subforum_el = row.select_one("div.subforums")
            if subforum_el:
                for sf_link in subforum_el.select("a"):
                    sf_href = _attr(sf_link, "href")
                    sf_match = re.search(r"forumid=(\d+)", sf_href)
                    if sf_match:
                        subforums.append({"id": int(sf_match.group(1)), "name": _text(sf_link)})

            current_forums.append(
                {
                    "id": fid,
                    "name": fname,
                    "description": fdesc,
                    "subforums": subforums,
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
                for sf in f.get("subforums", []):
                    lines.append(f"  - {sf['name']} (ID: {sf['id']})")
            lines.append("")
        return "\n".join(lines)