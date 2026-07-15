"""
LangGraph AgentState — only tracks messages and iteration count.
AgentMemory is managed externally (not serialized by LangGraph checkpoints).
"""

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """State that flows through the LangGraph agent graph.

    messages  — managed by LangGraph's add_messages reducer.
    iteration — current loop count (incremented by agent_node).
    """

    messages: Annotated[list[BaseMessage], add_messages]
    iteration: int
