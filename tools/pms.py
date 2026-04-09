from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from constants import BASE_URL
from helpers import _attr, _extract_id, _extract_page_count, _handle_error, _require_login_msg, _soup, _text, _tool_annotations
from models import GetPMInput, ListPMsInput
from session import SASession


def register_tools(mcp: FastMCP, session: SASession) -> None:
    @mcp.tool(
        name="sa_list_pms",
        annotations=_tool_annotations("List SA Private Messages"),
    )
    async def sa_list_pms(params: ListPMsInput) -> str:
        """List private messages in a Something Awful inbox folder."""
        url = f"{BASE_URL}/private.php"
        query: Dict[str, Any] = {
            "action": "show",
            "folderid": params.folder_id,
            "pagenumber": params.page,
        }
        try:
            resp = await session.get(url, params=query)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        if "loginform" in str(resp.url) or "account.php" in str(resp.url):
            return _require_login_msg()

        soup = _soup(resp.text)
        total_pages = _extract_page_count(soup)

        folder_names = {0: "Inbox", 1: "Sent"}
        folder_name = folder_names.get(params.folder_id, f"Folder {params.folder_id}")
        folder_el = soup.select_one(".selected-folder, .folder-name, option[selected]")
        if folder_el:
            folder_name = _text(folder_el) or folder_name

        messages: List[Dict[str, Any]] = []

        for row in soup.select("table#pm tr.pm, tr.privatemessage, .message-row"):
            link = row.select_one("a[href*='privatemessageid=']")
            if not link:
                continue
            href = _attr(link, "href")
            pm_id = _extract_id(href, "privatemessageid")

            subject = _text(link)
            sender_el = row.select_one(".sender, .from, td.sender, a[href*='userid']")
            sender = _text(sender_el)
            date_el = row.select_one(".date, td.date, .pmdate")
            date = _text(date_el)

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
        annotations=_tool_annotations("Read a SA Private Message"),
    )
    async def sa_get_pm(params: GetPMInput) -> str:
        """Read the full content of a Something Awful private message."""
        url = f"{BASE_URL}/private.php"
        query: Dict[str, Any] = {
            "action": "show",
            "privatemessageid": params.pm_id,
        }
        try:
            resp = await session.get(url, params=query)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        if "loginform" in str(resp.url) or "account.php" in str(resp.url):
            return _require_login_msg()

        soup = _soup(resp.text)

        subject_el = soup.select_one("h1, .subject, .pm-subject, title")
        subject = _text(subject_el).replace(" - Something Awful Forums", "").strip()

        sender_el = soup.select_one(".sender, .from, a[href*='userid']")
        sender = _text(sender_el)

        to_el = soup.select_one(".to, .recipient")
        recipient = _text(to_el)

        date_el = soup.select_one(".date, .pmdate")
        date = _text(date_el)

        body_el = soup.select_one(".postbody, .pm-body, .message-body, .body")
        if body_el:
            for quote in body_el.select(".bbc-block, blockquote, .quote"):
                author_el = quote.select_one("h4, .author, cite")
                qa = _text(author_el) if author_el else ""
                qa = re.sub(r"\s*posted:\s*$", "", qa, flags=re.IGNORECASE).strip()
                quote_link_el = quote.select_one("a.quote_link")
                post_ref = ""
                if quote_link_el:
                    href = quote_link_el.get("href", "") or ""
                    pid = re.search(r"postid=(\d+)", href)
                    if pid:
                        post_ref = f" (post #{pid.group(1)})"
                if author_el:
                    author_el.decompose()
                body_text = quote.get_text(" ", strip=True)
                label = f"[quote from {qa}{post_ref}]" if qa else "[quote]"
                quote.replace_with(f"\n{label} {body_text} [/quote]\n")
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