"""Durable session queue. PostgreSQL in deployment; explicit SQLite mock demo only."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .config import ROOT, mock_mode
from .contracts import ChatResponse, ExecutionEvent, SessionState, TranscriptEvent, TranscriptTurn, now

log = logging.getLogger(__name__)


class SessionConflict(ValueError):
    pass


class _SQLite:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        return self.connection.execute(query.replace('%s', '?').replace(' FOR UPDATE', ''), params)


class SessionStore:
    def __init__(self, mock_path: Path | None = None):
        self.mock = mock_mode()
        self.path = mock_path or Path(os.getenv('MOCK_STATE_PATH', str(ROOT / '.tmp/codex/mock/sessions.sqlite')))

    @contextmanager
    def connection(self):
        if self.mock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(':memory:', timeout=10) as con:
                con.row_factory = sqlite3.Row
                con.execute('ATTACH DATABASE ? AS app', (str(self.path),))
                con.execute('BEGIN IMMEDIATE')
                yield _SQLite(con)
        else:
            from .db import pool as get_pool
            with get_pool().connection() as con:
                with con.transaction():
                    yield con

    def initialize(self):
        if not self.mock:
            with self.connection() as con:
                con.execute('SELECT session_id FROM app.sessions LIMIT 0')
            return
        with self.connection() as con:
            for sql in (
                'CREATE TABLE IF NOT EXISTS app.sessions (session_id TEXT PRIMARY KEY,state TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,processed_revision INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT \'idle\',lease_owner TEXT,lease_until REAL,updated_at REAL NOT NULL)',
                'CREATE TABLE IF NOT EXISTS app.transcript_events (session_id TEXT NOT NULL,event_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(session_id,event_id))',
                'CREATE TABLE IF NOT EXISTS app.execution_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,trace_id TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,summary TEXT NOT NULL,timestamp TEXT NOT NULL)',
            ):
                con.execute(sql)

    def _load(self, con, session_id, lock=False):
        row = con.execute('SELECT state FROM app.sessions WHERE session_id=%s' + (' FOR UPDATE' if lock else ''), (session_id,)).fetchone()
        if not row:
            return None
        value = row['state']
        return SessionState.model_validate(json.loads(value) if isinstance(value, str) else value)

    def _ensure(self, con, session_id):
        state = SessionState(session_id=session_id)
        con.execute('INSERT INTO app.sessions(session_id,state,updated_at) VALUES(%s,%s,%s) ON CONFLICT(session_id) DO NOTHING', (session_id, state.model_dump_json(), time.time()))
        return self._load(con, session_id, lock=True)

    def _save(self, con, state):
        state.updated_at = now()
        con.execute('UPDATE app.sessions SET state=%s,revision=%s,processed_revision=%s,status=%s,updated_at=%s WHERE session_id=%s', (state.model_dump_json(), state.revision, state.processed_revision, state.status, time.time(), state.session_id))

    def get(self, session_id):
        with self.connection() as con:
            return self._load(con, session_id)

    def _event(self, con, state, kind, name, summary=''):
        con.execute('INSERT INTO app.execution_events(session_id,trace_id,kind,name,summary,timestamp) VALUES(%s,%s,%s,%s,%s,%s)', (state.session_id, state.trace_id, kind, name, summary[:500], now().isoformat()))

    def emit(self, state, kind, name, summary=''):
        with self.connection() as con:
            self._event(con, state, kind, name, summary)

    @staticmethod
    def _invalidate(state):
        state.guidance = None
        state.verified_facts = []
        state.documentation.passages = []
        state.revision += 1
        state.errors = []

    def ingest(self, event: TranscriptEvent):
        payload = event.model_dump_json()
        with self.connection() as con:
            state = self._ensure(con, event.session_id)
            prior = con.execute('SELECT payload FROM app.transcript_events WHERE session_id=%s AND event_id=%s', (event.session_id, event.event_id)).fetchone()
            if prior:
                raw = prior['payload']
                prior_event = TranscriptEvent.model_validate(json.loads(raw) if isinstance(raw, str) else raw)
                if prior_event.model_dump(exclude={'timestamp'}) != event.model_dump(exclude={'timestamp'}):
                    raise SessionConflict('event_id déjà utilisé avec un contenu différent')
                return state, 'duplicate'
            old = next((t for t in state.turns if t.turn_id == event.turn_id), None)
            if old and old.revision == event.revision and (old.text != event.text or old.is_final != event.is_final or old.speaker != event.speaker):
                raise SessionConflict('Une correction doit augmenter revision')
            con.execute('INSERT INTO app.transcript_events(session_id,event_id,payload) VALUES(%s,%s,%s)', (event.session_id, event.event_id, payload))
            if old and old.revision >= event.revision:
                return state, 'superseded'
            if state.ended and event.kind != 'end_call':
                raise SessionConflict('Appel terminé')
            if event.kind == 'end_call':
                state.ended = True
                self._event(con, state, 'call_ended', 'transcript', 'Appel terminé')
            else:
                turn = TranscriptTurn(**event.model_dump(exclude={'session_id', 'kind'}))
                if old:
                    state.turns[state.turns.index(old)] = turn
                else:
                    if len(state.turns) >= 200:
                        raise SessionConflict('Limite de 200 tours atteinte')
                    state.turns.append(turn)
            self._invalidate(state)
            state.status = 'queued' if event.is_final or event.kind == 'end_call' else 'interim'
            self._event(con, state, 'queued', 'transcript', f'Révision {state.revision}; final={event.is_final}')
            self._save(con, state)
            return state, 'accepted'

    def confirm(self, session_id, client_id):
        with self.connection() as con:
            state = self._ensure(con, session_id)
            if state.ended:
                raise SessionConflict('Appel terminé')
            if state.selected_client_id == client_id and state.customer_confirmed:
                return state
            state.selected_client_id = client_id
            state.customer_confirmed = True
            self._invalidate(state)
            state.status = 'queued' if any(t.is_final for t in state.turns) else 'idle'
            self._event(con, state, 'customer_confirmed', 'advisor', 'Sélection explicite du conseiller')
            self._save(con, state)
            return state

    def append_message(self, session_id, message, client_id):
        with self.connection() as con:
            state=self._ensure(con,session_id)
            if state.ended:
                raise SessionConflict('Appel terminé')
            if state.selected_client_id is not None and state.selected_client_id!=client_id:
                raise SessionConflict('Ce fil appartient à un autre client; ouvrir un nouveau fil')
            if len(state.turns)>=200:
                raise SessionConflict('Limite de 200 tours atteinte')
            state.selected_client_id=client_id
            state.customer_confirmed=True
            turn_id=str(uuid4())
            state.turns.append(TranscriptTurn(turn_id=turn_id,event_id=turn_id,revision=1,
                                              speaker='advisor',text=message,is_final=True,timestamp=now()))
            self._invalidate(state)
            state.status='queued'
            self._event(con,state,'queued','chat',f'Révision {state.revision}')
            self._save(con,state)
            return state

    def claim(self):
        owner = str(uuid4())
        with self.connection() as con:
            sql = "SELECT session_id FROM app.sessions WHERE status IN ('queued','processing') AND (lease_until IS NULL OR lease_until < %s) ORDER BY updated_at LIMIT 1"
            if not self.mock:
                sql += ' FOR UPDATE SKIP LOCKED'
            row = con.execute(sql, (time.time(),)).fetchone()
            if not row:
                return None
            state = self._load(con, row['session_id'])
            state.status = 'processing'
            state.trace_id = str(uuid4())
            con.execute('UPDATE app.sessions SET lease_owner=%s,lease_until=%s WHERE session_id=%s', (owner, time.time()+180, state.session_id))
            self._save(con, state)
            self._event(con, state, 'processing', 'orchestrator', f'Révision {state.revision}')
            return state, owner

    def finish(self, state, owner, result: ChatResponse):
        with self.connection() as con:
            current = self._load(con, state.session_id, lock=True)
            lease = con.execute('SELECT lease_owner FROM app.sessions WHERE session_id=%s', (state.session_id,)).fetchone()
            if lease['lease_owner'] != owner:
                return False
            con.execute('UPDATE app.sessions SET lease_owner=NULL,lease_until=NULL WHERE session_id=%s', (state.session_id,))
            if current.revision != state.revision:
                # A correction invalidates the in-flight result; the durable queue will claim again.
                return False
            state.guidance = result
            state.processed_revision = state.revision
            state.status = 'error' if result.status == 'error' else 'ended' if state.ended else 'ready'
            if state.ended and result.status != 'error':
                state.guidance.status = 'ended'
            self._save(con, state)
            self._event(con, state, 'failed' if result.status == 'error' else 'answer_produced', 'guidance', f'Révision {state.revision}')
            return True

    def events(self, session_id, after=0, limit=100):
        with self.connection() as con:
            rows = con.execute('SELECT * FROM app.execution_events WHERE session_id=%s AND event_id>%s ORDER BY event_id LIMIT %s', (session_id,after,min(limit,200))).fetchall()
            return [ExecutionEvent.model_validate(dict(r)) for r in rows]


async def worker(store: SessionStore):
    from .workflow import run_guidance
    while True:
        try:
            claimed = await asyncio.to_thread(store.claim)
            if not claimed:
                await asyncio.sleep(.25)
                continue
            state, owner = claimed
            try:
                result = await asyncio.to_thread(run_guidance, state, lambda kind,name,summary='': store.emit(state,kind,name,summary) if kind != 'answer_produced' else None)
            except Exception:
                log.error('Guidance failed; trace_id=%s', state.trace_id)
                state.errors.append('Traitement indisponible; consulter la trace')
                result = ChatResponse(text='Le traitement a échoué. Réessayez après vérification des services.',session_id=state.session_id,status='error',trace_id=state.trace_id,warnings=state.errors)
            await asyncio.to_thread(store.finish,state,owner,result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error('Session worker unavailable', exc_info=False)
            await asyncio.sleep(2)
