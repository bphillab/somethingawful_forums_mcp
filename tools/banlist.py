from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from constants import BASE_URL
from helpers import _attr, _extract_id, _handle_error, _require_login_msg, _soup, _text, _tool_annotations
from models import GetBanlistInput
from session import SASession


def register_tools(mcp: FastMCP, session: SASession) -> None:
    @mcp.tool(
        name="sa_get_banlist",
        annotations=_tool_annotations("Get SA Leper's Colony (Ban List)"),
    )
    async def sa_get_banlist(params: GetBanlistInput) -> str:
        """Fetch the Something Awful Leper's Colony (ban list).

        Returns banned/probated users with their ban type, date, reason,
        and the admin who issued the punishment. Supports filtering by
        ban type, admin, and month/year.
        """
        query: Dict[str, Any] = {
            "adminid": params.admin_id,
            "actfilt": params.ban_type,
            "ban_month": params.ban_month,
            "ban_year": params.ban_year,
            "pagenumber": params.page,
        }

        try:
            resp = await session.get(f"{BASE_URL}/banlist.php", params=query)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        if "loginform" in str(resp.url) or "account.php" in str(resp.url):
            return _require_login_msg()

        soup = _soup(resp.text)

        # Pagination info
        pages_el = soup.select_one(".pages[data-total-pages]")
        total_pages = int(pages_el["data-total-pages"]) if pages_el else 1
        current_page = int(pages_el["data-current-page"]) if pages_el else params.page

        # Total count from "Displaying jerks X to Y of Z"
        total_count = 0
        count_el = soup.select_one(".mqnav div[style='float:left']")
        if count_el:
            m = re.search(r"of\s+([\d,]+)", _text(count_el))
            if m:
                total_count = int(m.group(1).replace(",", ""))

        # Parse ban rows
        bans: List[Dict[str, Any]] = []
        ban_table = soup.select_one("table.standard.full")
        if ban_table:
            for row in ban_table.select("tr[data-postid]"):
                cells = row.select("td")
                if len(cells) < 6:
                    continue

                type_link = cells[0].select_one("a")
                ban_type_text = _text(type_link) if type_link else _text(cells[0])
                post_href = _attr(type_link, "href") if type_link else ""
                post_id = _extract_id(post_href, "postid") if post_href else int(row.get("data-postid", 0))

                date = _text(cells[1])

                user_link = cells[2].select_one("a")
                username = _text(user_link)
                user_id = _extract_id(_attr(user_link, "href"), "userid") if user_link else 0

                reason = _text(cells[3])

                requested_link = cells[4].select_one("a")
                requested_by = _text(requested_link)
                requested_by_id = _extract_id(_attr(requested_link, "href"), "userid") if requested_link else 0

                approved_link = cells[5].select_one("a")
                approved_by = _text(approved_link)
                approved_by_id = _extract_id(_attr(approved_link, "href"), "userid") if approved_link else 0

                bans.append({
                    "type": ban_type_text,
                    "date": date,
                    "username": username,
                    "user_id": user_id,
                    "reason": reason,
                    "requested_by": requested_by,
                    "requested_by_id": requested_by_id,
                    "approved_by": approved_by,
                    "approved_by_id": approved_by_id,
                    "post_id": post_id,
                })

        if not bans:
            return "No bans found for the given filters."

        if params.response_format == "json":
            return json.dumps({
                "page": current_page,
                "total_pages": total_pages,
                "total_count": total_count,
                "bans": bans,
            }, indent=2)

        lines = [f"# SA Leper's Colony (page {current_page} of {total_pages})"]
        if total_count:
            lines.append(f"*{total_count:,} total entries*")
        lines.append("")
        for b in bans:
            lines.append(f"## {b['username']} — {b['type']}")
            lines.append(f"- **Date**: {b['date']}")
            lines.append(f"- **Reason**: {b['reason']}")
            if b["requested_by"]:
                lines.append(f"- **By**: {b['requested_by']}")
            if b["post_id"]:
                lines.append(f"- **Post ID**: {b['post_id']}")
            lines.append("")
        return "\n".join(lines)
