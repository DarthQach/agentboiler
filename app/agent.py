import os
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

from app.capabilities import ToolRejectedRecoveryCapability
from app.config import settings


@dataclass
class AgentDeps:
    session_id: str
    user_id: str


def build_agent() -> Agent:
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)

    model = AnthropicModel(settings.agent_model)
    return Agent(
        model,
        deps_type=AgentDeps,
        system_prompt=settings.agent_system_prompt,
        capabilities=[ToolRejectedRecoveryCapability()],
    )


agent = build_agent()

import app.tools as _tools  # noqa: F401, E402
