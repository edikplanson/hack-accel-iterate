"""Agent API: client dashboard + LangGraph chat (full answer or SSE stream)."""
import json
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from . import data, db
from .db import jsonable, query, to_json
from .graph import graph


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if db._pool is not None:
        db._pool.close()


app = FastAPI(title="Advisor copilot", version="0.1.0", lifespan=lifespan)

BRIEF_PROMPT = (
    "Prepare my meeting brief for this client: situation in 3 lines, points of attention, "
    "sales opportunities, and 3 questions to ask them. Check the applicable internal rules."
)


RECURSION_LIMIT = 30


class ChatIn(BaseModel):
    client_id: int
    message: str = ""
    thread_id: str | None = None
    brief: bool = False  # True: meeting brief, message ignored


@app.get("/health")
def health():
    try:
        query("SELECT 1")
        return {"status": "ok", "db": True}
    except Exception as exc:
        return {"status": "degraded", "db": False, "error": str(exc)}


@app.get("/api/clients")
def clients(q: str | None = None, limit: int = 50):
    return jsonable(data.list_clients(q, min(limit, 200)))


@app.get("/api/clients/{client_id}")
def client_dashboard(client_id: int):
    d = data.dashboard(client_id)
    if d is None:
        raise HTTPException(404, f"client {client_id} not found")
    return jsonable(d)


@app.get("/api/clients/{client_id}/monthly")
def client_monthly(client_id: int, months: int = 12):
    return jsonable(data.monthly_spending(client_id, months))


def _text(content) -> str:
    """Text of a message: plain string or list of Anthropic blocks (only text blocks are kept)."""
    if isinstance(content, str):
        return content
    return "".join(
        b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type", "text") == "text"
    )


def _run(client_id: int, message: str, thread_id: str | None):
    thread_id = thread_id or f"client-{client_id}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    result = graph().invoke(
        {"messages": [HumanMessage(message)], "client_id": client_id},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT},
    )
    # Steps of this turn only (after the last human message)
    msgs = result["messages"]
    last_human = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
    steps, by_id = [], {}
    for m in msgs[last_human + 1:]:
        if isinstance(m, AIMessage):
            for call in m.tool_calls:
                step = {"tool": call["name"], "args": call["args"], "ok": None}
                steps.append(step)
                by_id[call["id"]] = step
        elif isinstance(m, ToolMessage) and m.tool_call_id in by_id:
            by_id[m.tool_call_id]["ok"] = '"error"' not in str(m.content)[:200]
    return {
        "thread_id": thread_id,
        "answer": _text(msgs[-1].content),
        "steps": steps,
        "latency_s": round(time.perf_counter() - t0, 2),
        "ui": keyword_ui(client_id, message),
    }


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    return _run(body.client_id, body.message, body.thread_id)


@app.post("/api/clients/{client_id}/brief")
def brief(client_id: int):
    return _run(client_id, BRIEF_PROMPT, None)


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


def keyword_ui(client_id: int, message: str) -> list[dict]:
    """Display orders triggered by keywords in the question. Values come from PostgreSQL, never from the LLM."""
    q = data_fold(message)
    out: list[dict] = []
    try:
        if _has(q, "depense", "evolution", "3 mois", "trois mois", "spend", "trend", "3 month", "three month"):
            # Last 3 full calendar months: the rolling window starts and ends on partial months
            current = data.reference_date().strftime("%Y-%m")
            rows = [r for r in data.monthly_spending(client_id, 4) if r["mois"] != current][-3:]
            out.append({"action": "chart", "chart_type": "line", "title": "Monthly spending (last 3 full months)",
                        "labels": [r["mois"] for r in rows], "values": [float(r["depenses"]) for r in rows],
                        "series_label": "Spending", "note": f"Source: transactions client {client_id} (PostgreSQL)"})
            out.append({"action": "highlight", "section": "depenses", "level": "positif",
                        "message": "Client's recent spending"})
        if _has(q, "risque", "incident", "risk"):
            rows = data.incidents_by_month(client_id, 6)
            out.append({"action": "chart", "chart_type": "bar", "title": "Payment incidents per month (6 months)",
                        "labels": [r["mois"] for r in rows], "values": [int(r["incidents"]) for r in rows],
                        "series_label": "Incidents", "unit": "count",
                        "note": f"Source: failed transactions, client {client_id} (PostgreSQL)"})
            out.append({"action": "highlight", "section": "dette", "level": "attention",
                        "message": "Debt level to watch"})
        if _has(q, "plafond", "carte", "limit", "card"):
            rows = data.card_spending_vs_limit(client_id)
            out.append({"action": "chart", "chart_type": "bar", "title": "30-day spending vs limit, per card",
                        "labels": [f"{r['card_brand']} {r['card_type']} {r['card_number_masked']}" for r in rows],
                        "values": [float(r["depenses_30j"]) for r in rows], "series_label": "30-day spending",
                        "values2": [float(r["credit_limit"] or 0) for r in rows], "series_label2": "Limit",
                        "note": f"Source: cards and transactions, client {client_id} (PostgreSQL)"})
            out.append({"action": "highlight", "section": "cartes", "level": "positif",
                        "message": "Client's cards and limits"})
        if _has(q, "produit", "propos", "product", "offer"):
            out.append({"action": "highlight", "section": "revenu", "level": "positif", "message": "Financial capacity"})
            out.append({"action": "highlight", "section": "dette", "level": "attention", "message": "Current debt"})
    except Exception as exc:  # the screen must never make the chat fail
        print(f"keyword_ui: {exc}", flush=True)
        return []
    # origin "db": values computed in PostgreSQL, the frontend only feeds these to the insight stage
    return [{**ui, "origin": "db"} for ui in out]


def data_fold(text: str) -> str:
    return text.lower().translate(str.maketrans("éèêëàâäîïôöùûüç", "eeeeaaaiioouuuc"))


def _sse(name: str, payload) -> str:
    return f"event: {name}\ndata: {to_json(payload)}\n\n"


def _stream(client_id: int, message: str, thread_id: str | None):
    """SSE generator: tool steps, display orders (ui) and tokens as they come, then the final answer."""
    thread_id = thread_id or f"client-{client_id}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    yield _sse("start", {"thread_id": thread_id})
    for ui in keyword_ui(client_id, message):
        yield _sse("ui", ui)
    answer = ""
    try:
        for mode, payload in graph().stream(
            {"messages": [HumanMessage(message)], "client_id": client_id},
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT},
            stream_mode=["updates", "messages"],
        ):
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") == "agent":
                    text = _text(chunk.content)
                    if text:
                        yield _sse("token", {"text": text})
                continue
            for update in (payload or {}).values():
                if not isinstance(update, dict):
                    continue
                for m in update.get("messages", []):
                    if isinstance(m, AIMessage):
                        if m.tool_calls:
                            for call in m.tool_calls:
                                yield _sse("step", {"id": call["id"], "tool": call["name"], "args": call["args"]})
                        else:
                            answer = _text(m.content)
                    elif isinstance(m, ToolMessage):
                        content = str(m.content)
                        ok = m.status != "error" and '"error"' not in content[:200]
                        yield _sse("step_done", {"id": m.tool_call_id, "ok": ok})
                        try:
                            parsed = json.loads(content)
                        except ValueError:
                            continue
                        if isinstance(parsed, dict) and "ui" in parsed:
                            yield _sse("ui", parsed["ui"])
        yield _sse("answer", {"answer": answer, "latency_s": round(time.perf_counter() - t0, 2), "thread_id": thread_id})
    except Exception as exc:
        yield _sse("error", {"message": str(exc)[:500]})


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn):
    message = BRIEF_PROMPT if body.brief else body.message.strip()
    if not message:
        raise HTTPException(400, "empty message")
    return StreamingResponse(
        _stream(body.client_id, message, body.thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
