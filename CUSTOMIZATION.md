## Changing the System Prompt

Location: `app/agent.py` — the `system_prompt=settings.agent_system_prompt` parameter on the `Agent(...)` constructor.

Default:

```python
return Agent(
    model,
    deps_type=AgentDeps,
    system_prompt=settings.agent_system_prompt,
    capabilities=[ToolRejectedRecoveryCapability()],
)
```

Custom inline prompt:

```python
return Agent(
    model,
    deps_type=AgentDeps,
    system_prompt="You are a support agent for a SaaS billing product. Answer briefly and ask before using tools.",
    capabilities=[ToolRejectedRecoveryCapability()],
)
```

The system prompt is a plain string. No special syntax is required.

If the prompt gets long, extract it to `app/prompts/system.txt` and load it from `app/agent.py`:

```python
from pathlib import Path

system_prompt = Path("app/prompts/system.txt").read_text()

return Agent(
    model,
    deps_type=AgentDeps,
    system_prompt=system_prompt,
    capabilities=[ToolRejectedRecoveryCapability()],
)
```

No other files need to change when updating the system prompt.

## Adding a New Tool

Tools are registered in `app/tools.py` with `@agent.tool`. `app/agent.py` imports that module at the bottom so the decorators run when the agent starts:

```python
agent = build_agent()

import app.tools as _tools  # noqa: F401, E402
```

Add the new tool in `app/tools.py` using the same pattern as `web_search`, `send_email`, and `create_file`:

```python
@agent.tool
async def get_weather(ctx: RunContext[AgentDeps], location: str) -> str:
    tool_name = "get_weather"
    await _require_approval(
        ctx.deps.user_id,
        ctx.deps.session_id,
        tool_name,
        {"location": location},
    )

    result = f"Weather for {location}: sunny, 24°C"
    _schedule_tool_call_increment(ctx.deps.user_id)
    return result
```

If the tool needs environment variables, add them to `app/.env` and `app/.env.example` with an inline comment:

```bash
WEATHER_API_KEY=weather_... # API key for the weather provider
```

Restart the backend:

```bash
uvicorn app.main:app --reload --port 8000
```

The approval queue, retry behavior, and usage tracking apply automatically to the new tool when it follows this pattern.

> The tool name used in the `@agent.tool` decorator is what appears in the approval UI and the `tool_approvals` table. Name it clearly — buyers will see it.

## Modifying Approval Logic

### Auto-approve specific tools

Location: `app/tools.py` — add an `AUTO_APPROVE_TOOLS` set near `_require_approval`.

```python
AUTO_APPROVE_TOOLS = {"web_search"}


async def _require_approval(
    user_id: str, session_id: str, tool_name: str, tool_args: dict[str, Any]
) -> None:
    await check_tool_call_limit(user_id)

    if tool_name in AUTO_APPROVE_TOOLS:
        return

    rejection_response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("tool_approvals")
        .select("id", count="exact")
        .eq("session_id", session_id)
        .eq("tool_name", tool_name)
        .eq("status", "rejected")
        .execute()
    )
```

Adding `"web_search"` means the tool executes without user approval. Use this for low-risk tools where the UX friction is not worth it.

### Change the retry limit

Location: `app/.env` → `AGENT_MAX_TOOL_RETRIES`.

Default behavior is 3 rejections. After 3 rejections, the agent gives up and responds without the tool.

`app/tools.py` reads the limit from `settings.agent_max_tool_retries`:

```python
if rejection_count >= settings.agent_max_tool_retries:
    raise ToolRejected("max rejections reached")
```

Set `AGENT_MAX_TOOL_RETRIES=1` for strict single-attempt behavior. Set it higher for more persistent agents.

⚠️ Setting this too high burns tokens fast if the user keeps rejecting.

### Skip approvals entirely

Location: `app/tools.py` — add `APPROVAL_REQUIRED = False` near `_require_approval`.

```python
APPROVAL_REQUIRED = False


async def _require_approval(
    user_id: str, session_id: str, tool_name: str, tool_args: dict[str, Any]
) -> None:
    await check_tool_call_limit(user_id)

    if not APPROVAL_REQUIRED:
        return

    rejection_response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("tool_approvals")
        .select("id", count="exact")
        .eq("session_id", session_id)
        .eq("tool_name", tool_name)
        .eq("status", "rejected")
        .execute()
    )
```

All tools execute immediately without writing to `tool_approvals`. Use this for internal tools where the developer trusts all tool calls.

⚠️ This disables the human-in-the-loop feature entirely — only do this intentionally.

## Swapping Supabase for Another Database

This requires changes in three places.

1. **`app/db.py`** — replace the Supabase client initialization with your database client. The rest of the backend currently imports `supabase_client` from this module, so this is the first place to swap implementation.

2. **Backend query methods** — there is no current `app/models.py`. Supabase calls are spread across routers, middleware, `app/approval_queue.py`, and `app/tools.py`. Before swapping databases, centralize those calls behind a small repository layer for `Session`, `ToolApproval`, `User`, and `Usage`, then replace the Supabase queries there.

3. **`frontend/`** — the frontend uses `@supabase/supabase-js` directly for approval API routes and the Realtime subscription on `tool_approvals`:

```javascript
supabase.channel().on("postgres_changes", ...)
```

If you swap the backend database, you must also replace these frontend integrations. The Realtime subscription in particular has no drop-in replacement — you need to implement polling or a WebSocket manually.

> Supabase Realtime is the hardest part to replace. If you're swapping the DB, consider keeping Supabase only for Realtime and using your preferred DB for persistence. It's a valid hybrid.
