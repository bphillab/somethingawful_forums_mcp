import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag
import httpx

BASE_URL = "https://forums.somethingawful.com"


def _handle_error(e: Exception) -> str:
    """Return a clear, actionable error message."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 403:
            return "Error: Access denied (403). You may need to log in first — call sa_login."
        if code == 404:
            return "Error: Page not found (404). Check the ID or URL."
        if code == 429:
            return "Error: Rate limited (429). Wait a moment before retrying."
        return f"Error: HTTP {code} from SA Forums."
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. SA Forums may be slow — try again."
    return f"Error: {type(e).__name__}: {e}"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _require_login_msg() -> str:
    return (
        "Not logged in. Call sa_login first, or ensure SA_USERNAME "
        "and SA_PASSWORD environment variables are set."
    )


def _page_from_redirect(url: str, fallback_soup: BeautifulSoup) -> int:
    """Extract the current page number from a redirect URL, falling back to HTML pagination."""
    m = re.search(r"pagenumber=(\d+)", url)
    if m:
        return int(m.group(1))
    current = _extract_current_page(fallback_soup)
    return current if current else 0


def _parse_posts(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Parse all posts from a SA thread page."""
    post_tables = soup.select("table.post, div.post, .postbody, tr.post")
    if not post_tables:
        post_tables = soup.select("[id^='post']")

    posts: List[Dict[str, Any]] = []
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
            for img in content_el.select("img"):
                label = img.get("title") or img.get("alt") or ""
                if label:
                    img.replace_with(f" {label} ")
                else:
                    img.decompose()
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
    return posts


def _extract_current_page(soup: BeautifulSoup) -> int:
    """Extract the current page number from SA pagination."""
    pages_el = soup.select_one(".pages, .pagenav")
    if pages_el:
        # SA marks the current page as a <span> or bold text without a link
        for el in pages_el.select("span, b"):
            text = el.get_text(strip=True)
            if text.isdigit():
                return int(text)
    return 0


def _extract_page_count(soup: BeautifulSoup) -> int:
    """Extract total page count from SA pagination."""
    pages_el = soup.select_one(".pages, .pagenav")
    if pages_el:
        page_links = pages_el.select("a")
        nums = []
        for a in page_links:
            href = a.get("href", "")
            m = re.search(r"pagenumber=(\d+)", href)
            if m:
                nums.append(int(m.group(1)))
        if nums:
            return max(nums)
    return 1


def _text(el: Optional[Tag]) -> str:
    if el is None:
        return ""
    return el.get_text(" ", strip=True)


def _attr(el: Optional[Tag], attr: str) -> str:
    if el is None:
        return ""
    return el.get(attr, "") or ""


def _clean_thread_title(text: str) -> str:
    """Normalize thread titles."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_thread_title_from_row(row: Optional[Tag]) -> str:
    """Extract the real thread title from an SA thread row."""
    if row is None:
        return ""

    title_link = row.select_one("td.title .info a.thread_title")
    if title_link:
        title = _clean_thread_title(_text(title_link))
        if title and title.lower() != "x":
            return title

    for candidate in row.select("a.thread_title"):
        cls = candidate.get("class", []) or []
        title = _clean_thread_title(_text(candidate))
        if not title or title.lower() == "x":
            continue
        if "x" in cls:
            continue
        return title

    return ""


def _extract_unread_link_from_row(row: Optional[Tag]) -> str:
    """Extract the goto=newpost link for a thread row, if present."""
    if row is None:
        return ""

    unread_link = row.select_one(".lastseen a.count[href*='goto=newpost']")
    if unread_link:
        href = _attr(unread_link, "href")
        if href:
            return f"{BASE_URL}/{href.lstrip('/')}"
    return ""


def _extract_unread_count_from_row(row: Optional[Tag]) -> int:
    """Extract unread count for a thread row, if present."""
    if row is None:
        return 0

    unread_count_el = row.select_one(".lastseen a.count b, .lastseen a.count")
    unread_count_text = _text(unread_count_el).replace(",", "")
    if unread_count_text.isdigit():
        return int(unread_count_text)
    return 0


def _extract_last_page_url_from_row(row: Optional[Tag]) -> str:
    """Extract a last-page or last-post URL from a thread row, if present."""
    if row is None:
        return ""

    lastpost_link = row.select_one(".title_pages a[href*='goto=lastpost']")
    if lastpost_link:
        href = _attr(lastpost_link, "href")
        if href:
            return f"{BASE_URL}/{href.lstrip('/')}"

    page_links = row.select(".title_pages a[href*='pagenumber=']")
    last_page_num = 0
    last_page_href = ""
    for a in page_links:
        href = _attr(a, "href")
        m = re.search(r"pagenumber=(\d+)", href)
        if m:
            page_num = int(m.group(1))
            if page_num > last_page_num:
                last_page_num = page_num
                last_page_href = href

    if last_page_href:
        return f"{BASE_URL}/{last_page_href.lstrip('/')}"

    return ""