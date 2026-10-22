"""LangGraph graph: an agent that reasons, calls the data tools, then answers citing its sources."""
import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .data import reference_date
from .tools import TOOLS

BANK_NAME = os.environ.get("BANK_NAME", "Horizon Bank")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

SYSTEM_PROMPT = """You are the copilot of {bank}'s bank advisors.
The advisor is working on client #{client_id}. Data reference date: {ref}.

You have two separate data sources:
1. Client data (PostgreSQL database): profile, cards, transactions, alerts. Tools get_client_profile, get_cards, get_spending_summary, get_recent_transactions, get_client_alerts, run_readonly_sql. They describe the client's situation.
2. Branch rules (OpenSearch database): the internal policy the advisor must apply, taken from the bank's official PDFs (common framework, card limits and operation, card blocking and disputes, overdraft and incident fees, credit eligibility, know your customer and AML/CFT, savings, anti-fraud monitoring). Tools search_bank_rules and get_bank_rule. The institution name in these documents may differ: they are our branch's rules and they are authoritative. They say what is allowed, forbidden, capped or charged, and the procedure to follow.
Never mix the two up: a client figure comes from PostgreSQL; a limit, threshold, fee or procedure comes from the rules.

Rules:
- Back every statement with the tools: client data for facts, branch rules for what is allowed or required.
- As soon as a question involves what is allowed, a limit, an overdraft, credit, card blocking, a fee, a KYC check or suspected fraud, first check the rules with search_bank_rules (English keyword query), follow their cross-references (refs) with get_bank_rule if needed, then compare the rule with the client's data. Never answer about a rule from memory: if no article matches, say so.
- Cite your sources in square brackets at the end of the relevant sentence, for example [clients#825], [cards#4524], [transactions client 825, 3 months] or [Card Limits and Card Operation §CARD-2.1].
- If data is missing, say so clearly instead of making it up.
- Amounts are in euros. Answer in English, concisely and action-oriented for the advisor: finding, risk or opportunity, proposed action.
- Never use em dashes or double hyphens in your answers.

You also drive the advisor's screen in real time with the ui_* tools. Use ui_highlight whenever you rely on an area already on screen (income, debt, score, spending, alerts, cards, categories, monthly, transactions). Use ui_show_chart when a numeric result is compared or changes over time, ui_show_table for a list of rows (suspicious transactions, SQL result), ui_show_metric for a key figure you computed. Call them as the analysis progresses, right after getting the data, never all at the end. 1 to 3 visual elements per answer. Never invent a value: only figures from the data tools. Do not copy into your text a table already on screen; point the advisor to the screen instead."""


def _system(client_id: int | None) -> SystemMessage:
    ref = reference_date()
    return SystemMessage(
        SYSTEM_PROMPT.format(
            bank=BANK_NAME,
            client_id=client_id if client_id is not None else "not specified",
            ref=ref.date().isoformat() if ref else "unknown",
        )
    )


class State(MessagesState):
    client_id: int | None


def build_graph():
    # No temperature: Sonnet 5.5 rejects non-default values (400). Adaptive thinking, on by default,
    # also uses max_tokens: leave headroom so the answer is not truncated.
    llm = ChatAnthropic(model=MODEL, max_tokens=8192).bind_tools(TOOLS)

    def agent(state: State):
        messages = [_system(state.get("client_id"))] + state["messages"]
        return {"messages": [llm.invoke(messages)]}

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    # Per-thread conversation memory (in RAM: enough for the demo)
    return g.compile(checkpointer=InMemorySaver())


_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
