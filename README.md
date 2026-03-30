# Something Awful Forums MCP Server

An MCP server that connects to the [Something Awful Forums](https://forums.somethingawful.com)
using your account credentials. It lets Claude browse threads, read posts, search
the forums, look up users, and manage your private messages.

## Setup

### 1. Install dependencies

```bash
pip install mcp httpx beautifulsoup4 pydantic
```

### 2. Set credentials as environment variables

```bash
export SA_USERNAME="your_username"
export SA_PASSWORD="your_password"
```

### 3. Configure in Claude Code / Cowork

Add this to your MCP config (e.g. `~/.claude/claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "sa_forums": {
      "command": "python3",
      "args": ["/path/to/sa_mcp/server.py"],
      "env": {
        "SA_USERNAME": "your_username",
        "SA_PASSWORD": "your_password"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `sa_login` | Authenticate with SA using your credentials |
| `sa_list_forums` | Browse all forum categories and sub-forums |
| `sa_list_threads` | List threads in a specific forum, including unread counts and jump-to-unread links when available |
| `sa_get_thread` | Read posts from a thread (paginated) |
| `sa_search` | Full-text search across all forums |
| `sa_get_user` | Look up a user's profile |
| `sa_list_pms` | Browse your private message inbox/folders |
| `sa_get_pm` | Read a specific private message |
| `sa_list_usercp_threads` | List threads in your user control panel, including unread counts and jump-to-unread links when available |
## Usage

After connecting the MCP server, start a conversation with:

> "Log in to SA and show me the forum list"

Claude will call `sa_login` first, then `sa_list_forums` — and you can go from there.

## Notes

- SA doesn't have a public API, so this server parses HTML. If SA updates their
  layout significantly, some tools may need selector updates.
- Be respectful of SA's servers — don't use this to scrape at high volume.
- Credentials are passed via environment variables and never stored by the server.
