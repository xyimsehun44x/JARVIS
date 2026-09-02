from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from jarvis.core.state import JarvisState


def build_graph(node, *, checkpointer=None):
    builder = StateGraph(JarvisState)
    builder.add_node("jarvis_core", node)
    builder.add_edge(START, "jarvis_core")
    builder.add_edge("jarvis_core", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())

