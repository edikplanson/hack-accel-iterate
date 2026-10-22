"""Current frontend contracts, backed by durable scoped advisor sessions."""
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import json
import os
import time
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import data, db
from .contracts import ChatResponse, TranscriptEvent
from .db import jsonable, query
from .sessions import SessionConflict, SessionStore, worker

store=SessionStore()


@asynccontextmanager
async def lifespan(_app):
    try:
        await asyncio.to_thread(store.initialize)
    except Exception:
        pass  # Explicit migrations; health remains available when initialization is missing.
    tasks=[asyncio.create_task(worker(store)) for _ in range(2)]
    _app.state.workers=tasks
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    if db._pool is not None:
        db._pool.close()
        db._pool=None


app=FastAPI(title='Copilote conseiller',version='0.2.0',lifespan=lifespan)
BRIEF_PROMPT='Prépare ma fiche de rendez-vous : situation, vigilance, besoins et questions. Vérifie les règles applicables.'


class ChatIn(BaseModel):
    client_id: int=Field(ge=0)
    message: str=Field(min_length=1,max_length=8000)
    thread_id: str | None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,128}$')


class CustomerConfirmation(BaseModel):
    client_id: int=Field(ge=0)


def require_token(name,authorization):
    expected=os.getenv(name,'')
    if not expected:
        raise HTTPException(503,'Accès à cette intégration non configuré')
    if not hmac.compare_digest(authorization or '', 'Bearer '+expected):
        raise HTTPException(401,'Authentification requise')


@app.exception_handler(SessionConflict)
async def conflict(request,exc):
    return JSONResponse(status_code=409,content={'detail':str(exc)})


@app.exception_handler(Exception)
async def unavailable(request,exc):
    return JSONResponse(status_code=503,content={'detail':'Service indisponible; vérifier connexions et migrations.'})


@app.get('/health')
def health():
    try:
        query('SELECT 1')
        return {'status':'ok','db':True,'model_enabled':os.getenv('MODEL_ENABLED','false').lower()=='true'}
    except Exception:
        return {'status':'degraded','db':False}


@app.get('/ready')
def ready():
    try:
        store.get('readiness-probe')
        query('SELECT client_id FROM clients LIMIT 1')
        from .documents import get_repository
        if get_repository().search('procedure',1).status=='unavailable':
            raise RuntimeError('search unavailable')
        if not getattr(app.state,'workers',[]) or any(t.done() for t in app.state.workers):
            raise RuntimeError('worker unavailable')
    except Exception:
        return JSONResponse(status_code=503,content={'status':'not_ready'})
    return {'status':'ready'}


@app.get('/api/clients')
def clients(q: str | None=Query(default=None,max_length=200),limit: int=Query(default=50,ge=1,le=200)):
    return jsonable(data.list_clients(q,limit))


@app.get('/api/clients/{client_id}')
def client_dashboard(client_id:int):
    result=data.dashboard(client_id)
    if result is None:
        raise HTTPException(404,'Client introuvable')
    return jsonable(result)


@app.get('/api/clients/{client_id}/monthly')
def client_monthly(client_id:int,months:int=Query(default=12,ge=1,le=60)):
    return jsonable(data.monthly_spending(client_id,months))


def client_exists(client_id):
    if not data.client_exists(client_id):
        raise HTTPException(404,'Client introuvable')


def frontend_response(state,elapsed):
    guidance=state.guidance or ChatResponse(text='Traitement en cours; reconnectez-vous à ce fil.',session_id=state.session_id,status='processing',trace_id=state.trace_id)
    payload=guidance.model_dump(mode='json')
    labels={'resolve_customer':'Vérification du dossier','customer_facts':'Données client','search_bank_rules':'search_bank_rules','claude_synthesis':'Synthèse Claude'}
    return {**payload,'thread_id':state.session_id,'answer':guidance.text+'\n\n'+'\n'.join('⚠ '+w for w in guidance.warnings[:3]),
            'steps':[{'tool':labels.get(name,name),'args':{},'ok':not (name=='customer_facts' and state.errors) and not (name=='search_bank_rules' and state.documentation.status=='unavailable')} for name in guidance.tool_calls_made],
            'latency_s':round(elapsed,2)}


async def _run(client_id,message,thread_id):
    await asyncio.to_thread(client_exists,client_id)
    started=time.perf_counter()
    state=await asyncio.to_thread(store.append_message,thread_id or f'client-{client_id}-{uuid4().hex[:12]}',message,client_id)
    revision=state.revision
    while time.perf_counter()-started<40:
        current=await asyncio.to_thread(store.get,state.session_id)
        if current.processed_revision>=revision and current.guidance:
            return frontend_response(current,time.perf_counter()-started)
        if current.revision>revision:
            break
        await asyncio.sleep(.1)
    return frontend_response(current,time.perf_counter()-started)


@app.post('/api/chat')
async def chat(body:ChatIn):
    if not body.message.strip():
        raise HTTPException(400,'Message vide')
    return await _run(body.client_id,body.message,body.thread_id)


@app.post('/api/clients/{client_id}/brief')
async def brief(client_id:int):
    return await _run(client_id,BRIEF_PROMPT,None)


def session_exists(session_id):
    state=store.get(session_id)
    if state is None:
        raise HTTPException(404,'Session introuvable')
    return state


@app.post('/api/sessions',status_code=201)
def create_session(authorization:str | None=Header(default=None)):
    require_token('ADVISOR_API_TOKEN',authorization)
    with store.connection() as con:
        return store._ensure(con,str(uuid4())).model_dump(mode='json')


@app.get('/api/sessions/{session_id}')
def get_session(session_id:str,authorization:str | None=Header(default=None)):
    require_token('ADVISOR_API_TOKEN',authorization)
    return session_exists(session_id).model_dump(mode='json')


@app.post('/api/sessions/{session_id}/customer')
def confirm_customer(session_id:str,body:CustomerConfirmation,authorization:str | None=Header(default=None)):
    require_token('ADVISOR_API_TOKEN',authorization)
    session_exists(session_id)
    client_exists(body.client_id)
    return store.confirm(session_id,body.client_id).model_dump(mode='json')


@app.post('/api/transcript-events',status_code=202)
def transcript_event(event:TranscriptEvent,authorization:str | None=Header(default=None)):
    require_token('TRANSCRIPT_TOKEN',authorization)
    state,disposition=store.ingest(event)
    return {'session_id':state.session_id,'revision':state.revision,'status':state.status,'disposition':disposition,'trace_id':state.trace_id}


@app.get('/api/sessions/{session_id}/events')
async def session_events(session_id:str,request:Request,after:int=Query(default=0,ge=0),format:str='sse',authorization:str | None=Header(default=None)):
    require_token('ADVISOR_API_TOKEN',authorization)
    await asyncio.to_thread(session_exists,session_id)
    try:
        cursor=max(0,int(request.headers.get('Last-Event-ID',str(after))))
    except ValueError:
        raise HTTPException(422,'Last-Event-ID invalide')
    if format=='json':
        return [event.model_dump(mode='json') for event in await asyncio.to_thread(store.events,session_id,cursor)]
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            events=await asyncio.to_thread(store.events,session_id,cursor)
            for event in events:
                cursor=event.event_id
                yield f'id: {cursor}\nevent: update\ndata: {json.dumps(event.model_dump(mode="json"),ensure_ascii=False)}\n\n'
            if not events:
                yield ': keepalive\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
