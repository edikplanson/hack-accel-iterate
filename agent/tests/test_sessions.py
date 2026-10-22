from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from agent.app.contracts import ChatResponse, TranscriptEvent
from agent.app.sessions import SessionStore, SessionConflict


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_MODE','mock')
    result = SessionStore(tmp_path/'state.sqlite')
    result.initialize()
    return result


def event(session='call-a', eid='event-1', turn='turn-1', revision=1, text='Je souhaite un rachat.', final=True):
    return TranscriptEvent(session_id=session,event_id=eid,turn_id=turn,revision=revision,speaker='customer',text=text,is_final=final,timestamp=datetime(2026,1,1,tzinfo=timezone.utc))


def answer(state, text='Conseil'):
    return ChatResponse(text=text,session_id=state.session_id,trace_id=state.trace_id)


def test_duplicate_and_changed_payload(store):
    state, disposition = store.ingest(event())
    again, duplicate = store.ingest(event())
    assert disposition=='accepted' and duplicate=='duplicate'
    assert state.revision==again.revision==1 and len(again.turns)==1
    with pytest.raises(SessionConflict):
        store.ingest(event(text='Autre contenu'))


def test_interim_then_final_and_superseded(store):
    store.ingest(event(final=False))
    assert store.claim() is None
    state,_ = store.ingest(event(eid='e2',revision=2))
    assert state.status=='queued' and state.turns[0].is_final
    old, disposition = store.ingest(event(eid='e3',revision=1))
    assert disposition=='superseded' and old.turns[0].revision==2
    assert store.claim() is not None


def test_correction_suppresses_stale_result(store):
    store.ingest(event())
    old, owner=store.claim()
    store.ingest(event(eid='e2',revision=2,text='Correction : aucun retrait.'))
    assert not store.finish(old,owner,answer(old,'Réponse périmée'))
    current=store.get('call-a')
    assert current.guidance is None and current.status=='queued'
    new,owner2=store.claim()
    assert store.finish(new,owner2,answer(new,'Réponse corrigée'))
    assert store.get('call-a').guidance.text=='Réponse corrigée'
    assert [e.kind for e in store.events('call-a')].count('answer_produced')==1


def test_recovery_and_lease_owner(store):
    store.ingest(event())
    state,owner=store.claim()
    assert store.claim() is None
    with store.connection() as con:
        con.execute('UPDATE app.sessions SET lease_until=%s WHERE session_id=%s',(0,'call-a'))
    recovered=SessionStore(store.path)
    recovered.initialize()
    new,new_owner=recovered.claim()
    assert owner!=new_owner
    assert not store.finish(state,owner,answer(state))
    assert recovered.finish(new,new_owner,answer(new))
    assert recovered.get('call-a').processed_revision==1


def test_concurrent_sessions_and_event_replay(store):
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(store.ingest,[event(session='a'),event(session='b',text='Autre client')]))
    first,owner=store.claim()
    second,_=store.claim()
    assert first.session_id!=second.session_id and first.turns[0].text!=second.turns[0].text
    store.finish(first,owner,answer(first))
    updates=store.events(first.session_id)
    assert all(e.session_id==first.session_id for e in updates)
    assert store.events(first.session_id,updates[-2].event_id)[0].event_id==updates[-1].event_id


def test_customer_confirmation_and_end_call(store):
    store.ingest(event())
    confirmed=store.confirm('call-a',825)
    assert confirmed.customer_confirmed and confirmed.selected_client_id==825
    end=event(eid='end',turn='end',text='').model_copy(update={'kind':'end_call'})
    state,_=store.ingest(end)
    assert state.ended
    with pytest.raises(SessionConflict):
        store.ingest(event(eid='late',turn='late'))
    state,owner=store.claim()
    store.finish(state,owner,answer(state))
    assert store.get('call-a').guidance.status=='ended'
