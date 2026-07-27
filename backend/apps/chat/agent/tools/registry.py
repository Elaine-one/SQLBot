"""
Tool definition and registry for the SQLBot Agent.

Tools are registered with a name, description, JSON Schema parameters,
and an async callable.  The registry provides OpenAI / Anthropic
function-calling schemas and dispatches execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from apps.chat.agent.memory import AgentMemory


@dataclass
class ToolDef:
    """Definition of a single agent tool.

    terminal     — success stops the agent loop.
    show_to_user — tool-call / tool-result events are pushed to the frontend
                   SSE stream so the user can see them (in the execution
                   details drawer and inline indicators).  Internal exploration
                   tools set this to False.
    """

    name: str
    description: str
    parameters: dict  # JSON Schema for the tool's arguments
    fn: Callable[..., Any]  # async fn(**kwargs, memory: AgentMemory) -> dict
    terminal: bool = False
    show_to_user: bool = False
    category: str = ""  # "explore" | "create" | "edit" | "execute" | "internal"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Global registry of agent tools."""

    _tools: dict[str, ToolDef] = {}

    # ── registration ──────────────────────────────────────

    @classmethod
    def register(cls, tool: ToolDef) -> None:
        if tool.name in cls._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Optional[ToolDef]:
        return cls._tools.get(name)

    # ── schema generation ─────────────────────────────────

    @classmethod
    def get_openai_schemas(cls) -> list[dict]:
        """Return all tools as OpenAI function-calling schemas."""
        return [t.to_openai_schema() for t in cls._tools.values()]

    @classmethod
    def get_openai_schemas_for(cls, names: list[str]) -> list[dict]:
        """Return schemas only for the named tools (order-preserving)."""
        return [
            t.to_openai_schema()
            for name in names
            if (t := cls._tools.get(name))
        ]

    # ── execution ─────────────────────────────────────────

    @classmethod
    async def execute(cls, name: str, args: dict, memory: AgentMemory) -> dict:
        """
        Execute a tool by name.

        The tool fn receives keyword arguments from `args` plus `memory`
        so it can read / write agent state.
        """
        tool = cls._tools.get(name)
        if not tool:
            return {
                "success": False,
                "error": f"Unknown tool: {name}",
                "available_tools": sorted(cls._tools.keys()),
            }
        try:
            return await tool.fn(**args, memory=memory)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @classmethod
    def count(cls) -> int:
        return len(cls._tools)

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._tools.keys())

    @classmethod
    def is_user_visible(cls, name: str) -> bool:
        """Return True if this tool's events should be emitted to the frontend."""
        tool = cls._tools.get(name)
        return tool is not None and tool.show_to_user

    @classmethod
    def get_user_visible_schemas(cls) -> list[dict]:
        """Return OpenAI schemas only for tools marked show_to_user=True."""
        return [t.to_openai_schema() for t in cls._tools.values() if t.show_to_user]

    @classmethod
    def get_terminal_tool_names(cls) -> list[str]:
        """Return names of all terminal tools (single source of truth)."""
        return sorted(name for name, t in cls._tools.items() if t.terminal)
