import re
from typing import Optional

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