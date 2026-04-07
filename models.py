from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    # No fields — credentials come from env vars


class ListForumsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class ListThreadsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    forum_id: int = Field(
        ...,
        description="The SA forum ID (e.g. 46 for FYAD). Find IDs via sa_list_forums.",
        ge=1,
    )
    page: int = Field(default=1, description="Page number to fetch", ge=1)
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class GetThreadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    thread_id: Optional[int] = Field(
        default=None,
        description="The SA thread ID. Found in thread URLs as threadid=X. Required unless goto_post_id is set.",
        ge=1,
    )
    page: int = Field(default=1, description="Page of posts to fetch", ge=1)
    last_page: bool = Field(
        default=False,
        description="If true, fetch the thread's last page instead of the page in 'page'.",
    )
    goto_newpost: bool = Field(
        default=False,
        description="If true, follow the thread's goto=newpost link and fetch the page containing the first unread post.",
    )
    goto_post_id: Optional[int] = Field(
        default=None,
        description="If set, jump directly to this post ID (uses goto=post&postid=X). Fetches the page containing that post. thread_id is not required when this is set.",
        ge=1,
    )
    since_post_id: Optional[int] = Field(
        default=None,
        description="If set, only return posts with an ID greater than this value. Useful combined with goto_post_id to get only posts after a known post.",
        ge=1,
    )
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class SearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(
        ...,
        description=(
            "Search terms to find in SA threads and posts. "
            "Supports SA search operators: intitle:word, username:goon, "
            "since:YYYY-MM-DD, before:YYYY-MM-DD, threadid:12345, quoting:goon."
        ),
        min_length=1,
        max_length=300,
    )
    forum_id: Optional[int] = Field(
        default=None,
        description="Restrict search to a specific forum ID. Leave blank to search all forums.",
        ge=1,
    )
    user: Optional[str] = Field(
        default=None,
        description=(
            "Restrict search to posts by this SA username. "
            "Appended to the query as the username: operator."
        ),
        max_length=100,
    )
    title: Optional[str] = Field(
        default=None,
        description="Search within thread titles only. Appended as intitle:\"...\".",
        max_length=300,
    )
    since: Optional[str] = Field(
        default=None,
        description="Restrict results to posts on or after this date. Accepts YYYY-MM-DD or relative strings like \"last monday\" or \"2 days ago\". Appended as since:\"...\".",
        max_length=100,
    )
    before: Optional[str] = Field(
        default=None,
        description="Restrict results to posts before this date. Accepts YYYY-MM-DD or relative strings like \"last monday\" or \"2 days ago\". Appended as before:\"...\".",
        max_length=100,
    )
    quoting: Optional[str] = Field(
        default=None,
        description="Restrict results to posts quoting this SA username. Appended as quoting:\"...\".",
        max_length=100,
    )
    userid: Optional[int] = Field(
        default=None,
        description="Restrict search to posts by this SA user ID. Appended as userid:X.",
        ge=1,
    )
    threadid: Optional[int] = Field(
        default=None,
        description="Restrict results to a specific thread ID. Appended as threadid:X.",
        ge=1,
    )
    resolve_thread_ids: bool = Field(
        default=False,
        description="If true, follow post redirects to resolve missing thread IDs. Makes one extra request per result with a missing thread ID — use sparingly.",
    )
    page: int = Field(default=1, description="Page of results to fetch", ge=1)
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class GetUserInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    username: Optional[str] = Field(
        default=None,
        description="SA username to look up. Provide either username or user_id.",
        max_length=100,
    )
    user_id: Optional[int] = Field(
        default=None,
        description="SA user ID to look up. Provide either username or user_id.",
        ge=1,
    )
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class ListPMsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    folder_id: int = Field(
        default=0,
        description="PM folder ID. 0 = Inbox (default), 1 = Sent, other numbers for custom folders.",
        ge=0,
    )
    page: int = Field(default=1, description="Page of messages to fetch", ge=1)
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class GetPMInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    pm_id: int = Field(
        ...,
        description="Private message ID. Found via sa_list_pms.",
        ge=1,
    )
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )


class ListUserCPThreadsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (default) or 'json'",
        pattern="^(markdown|json)$",
    )