# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An MCP (Model Context Protocol) server that gives Claude authenticated access to Something Awful Forums via HTML scraping (no official API exists). The server exposes tools for browsing forums, reading threads, searching, managing PMs, and fetching user profiles.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run MCP server (default port 8080)
python server.py

# Run health check server (port 8000)
python health_check.py
```

Required env vars: `SA_USERNAME`, `SA_PASSWORD`. Optional: `PORT` (defaults to 8080).

There is no test framework. Manual testing is done by running the server and invoking tools via an MCP client (e.g., Claude Desktop).

## Architecture

### Request Flow

```
MCP Client → server.py (FastMCP) → tools/<module>.py → session.py (httpx) → forums.somethingawful.com
                                         ↓
                               helpers.py + BeautifulSoup HTML parsing
                                         ↓
                               markdown or JSON response
```

### Key Modules

- **server.py** — FastMCP app entry point; calls each tool module's `register_tools()` at startup; manages session lifecycle via lifespan context manager; exposes `/health` endpoint.
- **session.py** — Single shared `SASession` wrapping `httpx.AsyncClient`; holds login cookies for all requests; `login()` POSTs credentials and detects success by checking for "logout" in response HTML.
- **models.py** — Pydantic input models for all tools; all inherit from `SABaseModel` (strips whitespace, strict validation); every model has a `response_format` field ("markdown" | "json").
- **helpers.py** — Shared HTML parsing utilities: `_soup()`, `_parse_posts()`, `_handle_error()`, `_extract_id()`, `_tool_annotations()`.
- **tools/** — One file per feature area; each exports `register_tools(mcp, session)`.

### Tool Registration Pattern

Every tool module follows this structure:

```python
def register_tools(mcp: FastMCP, session: SASession) -> None:
    @mcp.tool(name="sa_...", annotations=_tool_annotations("...", read_only=True))
    async def sa_tool_name(params: ToolInput) -> str:
        try:
            resp = await session.get(url)
            resp.raise_for_status()
            # parse HTML with BeautifulSoup + helpers
            # return markdown or JSON based on params.response_format
        except Exception as e:
            return _handle_error(e)
```

### HTML Parsing Conventions

- Always use multiple CSS selector fallbacks to handle SA layout variations:  
  `el.select_one(".author, .username, td.userinfo .author")`
- `_text(el)` and `_attr(el, attr)` return `""` for `None` elements (null-safe wrappers).
- `_parse_posts()` in helpers.py is the central post-parsing function; handles quotes, images, and author extraction.
- When SA returns a redirect to the login page instead of a 401, check the response URL or page content for a login form.

### Concurrency

- `asyncio.gather()` is used in `sa_get_thread_info` (fetches page 1 and last page simultaneously) and `sa_search` (resolves thread IDs in parallel).
- The `SASession` instance is shared across all tools and is not thread-safe, but FastMCP's async model handles this correctly.

### Response Format

All tools support dual output via `params.response_format`:
- `"markdown"` (default) — human-readable with headers, bullets, bold text
- `"json"` — structured dict/list serialized with `json.dumps(result, indent=2)`
