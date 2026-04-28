from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from app.exceptions import ToolRejected


class ToolRejectedRecoveryCapability(AbstractCapability[Any]):
    async def on_tool_execute_error(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        error: Exception,
    ) -> Any:
        if isinstance(error, ToolRejected):
            return f"[rejected] {call.tool_name}: {error}"

        raise error
