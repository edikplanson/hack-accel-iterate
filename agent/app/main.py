"""API de l'agent : tableau de bord client + chat LangGraph (réponse complète ou flux SSE)."""
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


app = FastAPI(title="Copilote conseiller", version="0.1.0", lifespan=lifespan)

BRIEF_PROMPT = (
    "Prépare ma fiche de rendez-vous pour ce client : situation en 3 lignes, points de vigilance, "
    "opportunités commerciales, et 3 questions à lui poser. Vérifie les règles internes applicables."
)


RECURSION_LIMIT = 30


class ChatIn(BaseModel):
    client_id: int
    message: str = ""
    thread_id: str | None = None
    brief: bool = False  # True : fiche de rendez-vous, message ignoré


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
        raise HTTPException(404, f"client {client_id} introuvable")
    return jsonable(d)


@app.get("/api/clients/{client_id}/monthly")
def client_monthly(client_id: int, months: int = 12):
    return jsonable(data.monthly_spending(client_id, months))


def _text(content) -> str:
    """Texte d'un message : chaîne simple ou liste de blocs Anthropic (on ne garde que les blocs texte)."""
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
    # Étapes de ce tour uniquement (après le dernier message humain)
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
    }


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.message.strip():
        raise HTTPException(400, "message vide")
    return _run(body.client_id, body.message, body.thread_id)


@app.post("/api/clients/{client_id}/brief")
def brief(client_id: int):
    return _run(client_id, BRIEF_PROMPT, None)


def _sse(name: str, payload) -> str:
    return f"event: {name}\ndata: {to_json(payload)}\n\n"


def _stream(client_id: int, message: str, thread_id: str | None):
    """Générateur SSE : étapes d'outils, ordres d'affichage (ui) et tokens au fil de l'eau, puis la réponse finale."""
    thread_id = thread_id or f"client-{client_id}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    yield _sse("start", {"thread_id": thread_id})
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
        raise HTTPException(400, "message vide")
    return StreamingResponse(
        _stream(body.client_id, message, body.thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
