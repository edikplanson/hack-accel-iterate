"""Tools exposed to the LangGraph agent. Each tool returns JSON with its sources."""
import re
from typing import Literal

from langchain_core.tools import tool

from . import data, rules
from .db import query, query_readonly, to_json


@tool
def get_client_profile(client_id: int) -> str:
    """Client profile: age, yearly income, total debt, debt-to-income ratio, credit score, number of cards."""
    p = data.client_profile(client_id)
    if not p:
        return to_json({"error": f"client {client_id} not found"})
    return to_json({"source": f"clients#{client_id}", "profile": p})


@tool
def get_cards(client_id: int) -> str:
    """Client's bank cards: brand, type, credit limit, expiry, chip, compromised flag."""
    return to_json({"source": f"cards (client {client_id})", "cards": data.client_cards(client_id)})


@tool
def get_spending_summary(client_id: int, months: int = 3) -> str:
    """Spending summary over N months: total, refunds, top categories, payment incidents."""
    s = data.spending_summary(client_id, months)
    return to_json({"source": f"transactions (client {client_id}, {months} months)", **s})


@tool
def get_recent_transactions(client_id: int, limit: int = 20, only_incidents: bool = False) -> str:
    """Client's latest transactions. only_incidents=True to see only failed payments."""
    rows = data.recent_transactions(client_id, limit, only_incidents)
    return to_json({"source": f"transactions (client {client_id})", "transactions": rows})


@tool
def get_client_alerts(client_id: int) -> str:
    """Client's active alerts: compromised card, expired card, payments declined for insufficient balance, low credit score."""
    return to_json({"alerts": data.client_alerts(client_id)})


@tool
def search_bank_rules(question: str, k: int = 5, doc_code: str | None = None) -> str:
    """Search the bank's internal rules (PDF policies indexed in OpenSearch) for relevant articles.
    Use precise English keywords (e.g. "overdraft authorised limit fees", "card blocking stolen card",
    "credit debt-to-income ratio").
    doc_code restricts the search to one document: REF (common framework), CARD (card limits), BLK (card blocking, disputes),
    OVD (overdraft, incident fees), LOAN (credit eligibility), KYC (know your customer, AML/CFT),
    SAV (savings), FRD (monitoring, anti-fraud). Each article has a source to cite and cross-references (refs)."""
    try:
        return to_json({"clauses": rules.search(question, max(1, min(int(k), 10)), doc_code)})
    except Exception as exc:
        return to_json({"error": f"rules unavailable: {exc}"})


@tool
def get_bank_rule(article_ids: list[str]) -> str:
    """Full text of internal rule articles by id (e.g. ["CARD-2.1", "OVD-3.2"]).
    Use it to follow the cross-references (refs) of an article found with search_bank_rules."""
    try:
        return to_json({"clauses": rules.get_articles([str(a).upper() for a in article_ids[:10]])})
    except Exception as exc:
        return to_json({"error": f"rules unavailable: {exc}"})


_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do)\b", re.I)


@tool
def run_readonly_sql(sql: str) -> str:
    """Free-form read-only SQL query (SELECT or WITH) on the clients, cards and transactions tables.
    Use it only when no other tool answers the question. At most 200 rows."""
    clean = sql.strip().rstrip(";")
    if ";" in clean or not re.match(r"^(select|with)\b", clean, re.I) or _FORBIDDEN.search(clean):
        return to_json({"error": "only single SELECT queries are allowed"})
    try:
        rows = query_readonly(clean)
    except Exception as exc:
        return to_json({"error": str(exc)})
    return to_json({"source": "SQL query", "sql": clean, "rows": rows})


# ---------- Screen tools: they do not read the database, they describe a display change ----------

Level = Literal["critical", "warning", "info", "positive"]
Section = Literal["income", "debt", "score", "spending", "alerts", "cards", "categories", "monthly", "transactions"]

# The frontend keeps French ids for levels and dashboard sections (data-section): map the English values to them
_LEVEL_ID = {"critical": "critique", "warning": "attention", "info": "info", "positive": "positif"}
_SECTION_ID = {"income": "revenu", "debt": "dette", "score": "score", "spending": "depenses", "alerts": "alertes",
               "cards": "cartes", "categories": "categories", "monthly": "mensuel", "transactions": "transactions"}


def _ui(payload: dict) -> str:
    return to_json({"ok": True, "ui": payload})


@tool
def ui_show_chart(
    title: str,
    labels: list[str],
    values: list[float],
    chart_type: Literal["bar", "line", "pie"] = "bar",
    series_label: str = "",
    note: str = "",
) -> str:
    """Show a chart on the advisor's screen.
    Use it whenever a numeric result is compared (categories, cards, clients) or changes over time (months).
    labels and values must have the same length (24 points at most) and come from a data tool.
    chart_type: "bar" to compare, "line" for a trend, "pie" for a breakdown.
    note: one short sentence saying what to take away from the chart."""
    n = min(len(labels), len(values), 24)
    return _ui({
        "action": "chart",
        "title": title,
        "chart_type": chart_type,
        "labels": [str(x) for x in labels[:n]],
        "values": [float(v) for v in values[:n]],
        "series_label": series_label,
        "note": note,
    })


@tool
def ui_highlight(section: Section, message: str, level: Level = "warning") -> str:
    """Outline an area already shown on the dashboard and add a note to it.
    Use it whenever your reasoning relies on one of these areas:
    income, debt, score (credit score), spending (30-day spending), alerts, cards,
    categories (top 30-day spending), monthly (monthly spending chart), transactions (recent transactions).
    message: one short sentence explaining why the area matters. level: critical, warning, info or positive."""
    return _ui({"action": "highlight", "section": _SECTION_ID.get(section, section), "message": message,
                "level": _LEVEL_ID.get(level, "info")})


@tool
def ui_show_table(title: str, columns: list[str], rows: list[list[str | int | float | None]], note: str = "") -> str:
    """Show a table on the advisor's screen.
    Use it for a list of rows: suspicious transactions, payment incidents, SQL query results.
    Each row has as many cells as columns. 20 rows at most, values taken from the data tools."""
    clean = [["" if c is None else str(c) for c in r] for r in rows[:20]]
    return _ui({"action": "table", "title": title, "columns": [str(c) for c in columns], "rows": clean, "note": note})


@tool
def ui_show_metric(label: str, value: str, detail: str = "", level: Level = "info") -> str:
    """Show a key figure in large type on the advisor's screen.
    Use it for an indicator you computed or extracted (debt ratio, spending increase, number of incidents).
    value is already formatted (e.g. "€2,340", "+18%"). detail: one sentence of context. level: critical, warning, info or positive."""
    return _ui({"action": "metric", "label": label, "value": value, "detail": detail, "level": _LEVEL_ID.get(level, "info")})


TOOLS = [
    get_client_profile,
    get_cards,
    get_spending_summary,
    get_recent_transactions,
    get_client_alerts,
    search_bank_rules,
    get_bank_rule,
    run_readonly_sql,
    ui_show_chart,
    ui_highlight,
    ui_show_table,
    ui_show_metric,
]
