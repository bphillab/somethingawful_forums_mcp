from __future__ import annotations

import json
import re
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from helpers import _attr, _handle_error, _require_login_msg, _soup, _text
from models import GetUserInput
from session import SASession

BASE_URL = "https://forums.somethingawful.com"


def register_tools(mcp: FastMCP, session: SASession) -> None:
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
        """Look up a Something Awful user's profile."""
        if not params.username and not params.user_id:
            return "Error: Provide either 'username' or 'user_id'."

        query: Dict[str, Any] = {"action": "getinfo"}
        if params.user_id:
            query["userid"] = params.user_id
        else:
            query["username"] = params.username

        url = f"{BASE_URL}/member.php"
        try:
            resp = await session.get(url, params=query)
            resp.raise_for_status()
        except Exception as e:
            return _handle_error(e)

        if "loginform" in str(resp.url) or "account.php" in str(resp.url):
            return _require_login_msg()

        soup = _soup(resp.text)

        uid = params.user_id or 0
        uid_match = re.search(r"userid=(\d+)", resp.text)
        if uid_match:
            uid = int(uid_match.group(1))

        username_el = soup.select_one("h1, .username, .profile-username, title")
        username = _text(username_el).replace(" - Something Awful Forums", "").strip()
        if params.username and not username:
            username = params.username

        profile: Dict[str, str] = {}
        for row in soup.select("dl.profileinfo dt, table.profileinfo tr, .profile-row"):
            label_el = row.select_one("dt, th, .label")
            value_el = row.select_one("dd, td:last-child, .value")
            if label_el and value_el:
                label = _text(label_el).lower().rstrip(":").strip()
                value = _text(value_el)
                profile[label] = value

        joined = profile.get("joined", profile.get("member since", profile.get("registered", "")))
        posts = profile.get("posts", profile.get("post count", ""))
        title = profile.get("title", profile.get("custom title", ""))
        location = profile.get("location", profile.get("hometown", ""))
        bio = profile.get("biography", profile.get("bio", profile.get("about", "")))

        avatar_el = soup.select_one(".avatar img, .profile-avatar img, img.avatar")
        avatar_url = _attr(avatar_el, "src") if avatar_el else ""

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