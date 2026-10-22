"""API de l'agent : tableau de bord client + chat LangGraph."""
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from . import data, db
from .db import jsonable, query
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


class ChatIn(BaseModel):
    client_id: int
    message: str
    thread_id: str | None = None


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


def _run(client_id: int, message: str, thread_id: str | None):
    thread_id = thread_id or f"client-{client_id}-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    result = graph().invoke(
        {"messages": [HumanMessage(message)], "client_id": client_id},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": 16},
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
    answer = msgs[-1].content
    if isinstance(answer, list):  # blocs de contenu Anthropic
        answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))
    return {
        "thread_id": thread_id,
        "answer": answer,
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
