"""A fixed, bounded LangGraph; retrieval cannot widen the confirmed customer scope."""
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .contracts import ChatResponse, SessionState


class State(TypedDict):
    session: SessionState
    emit: Callable
    response: ChatResponse
    categories: list[str]
    policy_query: str | None
    calls: list[str]
    warnings: list[str]


def build_graph():
    from .workflow import prepare, retrieve, compose
    g=StateGraph(State)
    g.add_node('resolve_issue',prepare)
    g.add_node('retrieve_evidence',retrieve)
    g.add_node('advisor_guidance',compose)
    g.add_edge(START,'resolve_issue')
    g.add_edge('resolve_issue','retrieve_evidence')
    g.add_edge('retrieve_evidence','advisor_guidance')
    g.add_edge('advisor_guidance',END)
    return g.compile()


_graph=None


def graph():
    global _graph
    if _graph is None:
        _graph=build_graph()
    return _graph
