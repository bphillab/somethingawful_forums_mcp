from __future__ import annotations

import json

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

from helpers import _handle_error, _tool_annotations
from models import FetchEmbedInput
from session import SASession

_EMBED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SAForumsReader/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _og(soup: BeautifulSoup, prop: str) -> str:
    el = soup.find("meta", property=f"og:{prop}")
    if not el:
        el = soup.find("meta", attrs={"name": f"og:{prop}"})
    return (el.get("content") or "").strip() if el else ""


def _tc(soup: BeautifulSoup, name: str) -> str:
    el = soup.find("meta", attrs={"name": f"twitter:{name}"})
    return (el.get("content") or "").strip() if el else ""


def register_tools(mcp: FastMCP, session: SASession) -> None:
    @mcp.tool(
        name="sa_fetch_embed",
        annotations=_tool_annotations("Fetch URL Embed Preview"),
    )
    async def sa_fetch_embed(params: FetchEmbedInput) -> str:
        """Fetch title, description, and image metadata from a URL found in a post.

        Reads Open Graph and Twitter Card tags. Useful for previewing imgur,
        Bluesky, Twitter/X, YouTube, and most other modern sites without
        needing a full browser. Does not use SA credentials."""
        if not params.url.startswith(("http://", "https://")):
            return "Error: Only http and https URLs are supported."

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=10,
                headers=_EMBED_HEADERS,
            ) as client:
                resp = await client.get(params.url)
                resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        soup = BeautifulSoup(resp.text, "html.parser")

        title = (
            _og(soup, "title")
            or _tc(soup, "title")
            or (soup.title.get_text(strip=True) if soup.title else "")
        )
        description = _og(soup, "description") or _tc(soup, "description")
        image = _og(soup, "image") or _tc(soup, "image")
        site_name = _og(soup, "site_name")
        author = _og(soup, "article:author") or _tc(soup, "creator")
        url_canonical = _og(soup, "url") or str(resp.url)

        embed = {
            "url": url_canonical,
            "site_name": site_name,
            "title": title,
            "description": description,
            "image": image,
            "author": author,
        }
        # Drop empty fields
        embed = {k: v for k, v in embed.items() if v}

        if not any(k in embed for k in ("title", "description")):
            return f"No embed metadata found at {params.url}"

        if params.response_format == "json":
            return json.dumps(embed, indent=2)

        lines = []
        if site_name:
            lines.append(f"**{site_name}**")
        if title:
            lines.append(f"### {title}")
        if author:
            lines.append(f"*by {author}*")
        if description:
            lines.append(description)
        if image:
            lines.append(f"\n*Image: {image}*")
        return "\n".join(lines)