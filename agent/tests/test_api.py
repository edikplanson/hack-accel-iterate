import time

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def client(records,monkeypatch,tmp_path):
    from agent.app import main,data
    from agent.app.sessions import SessionStore
    monkeypatch.setattr(main,'store',SessionStore(tmp_path/'sessions.sqlite'))
    monkeypatch.setattr(main,'query',lambda *args:[])
    monkeypatch.setattr(data,'list_clients',lambda *args:[{'client_id':825,'age':40,'nb_cards':1}])
    monkeypatch.setattr(data,'dashboard',lambda cid:{'profil':records[cid],'cartes':[],'kpis':{},'alertes':[],'dernieres_transactions':[],'categories_30j':[]} if cid in records else None)
    with TestClient(main.app) as value:
        yield value


def settled(client,sid):
    for _ in range(100):
        state=client.get('/api/sessions/'+sid,headers={'Authorization':'Bearer advisor-test'}).json()
        if state['guidance'] and state['revision']==state['processed_revision']:return state
        time.sleep(.05)
    pytest.fail('Worker did not finish')


def test_existing_frontend_contracts_and_persistent_thread(client):
    assert client.get('/health').json()['db'] is True
    assert client.get('/api/clients').json()[0]['client_id']==825
    assert client.get('/api/clients/825').json()['profil']['client_id']==825
    assert isinstance(client.get('/api/clients/825/monthly').json(),list)
    first=client.post('/api/chat',json={'client_id':825,'message':'Quels sont les risques de ce client ?'})
    assert first.status_code==200
    result=first.json()
    assert {'thread_id','answer','steps','latency_s'}<=result.keys()
    assert '48000' in result['answer'] and result['thread_id'].startswith('client-825-')
    assert result['steps'] and all({'tool','args','ok'}<=s.keys() for s in result['steps'])
    second=client.post('/api/chat',json={'client_id':825,'message':'Et sa carte ?','thread_id':result['thread_id']})
    assert second.status_code==200 and second.json()['thread_id']==result['thread_id']
    saved=settled(client,result['thread_id'])
    assert len(saved['turns'])==2 and saved['customer_confirmed']
    assert client.post('/api/chat',json={'client_id':826,'message':'Autre client','thread_id':result['thread_id']}).status_code==409
    assert client.post('/api/clients/825/brief').status_code==200


def test_normalized_transcript_requires_confirmation_and_token(client):
    headers={'Authorization':'Bearer advisor-test'}
    sid=client.post('/api/sessions',headers=headers).json()['session_id']
    event={'session_id':sid,'event_id':'e1','turn_id':'t1','revision':1,'speaker':'customer','text':'Mon identifiant est 825. Quel est le plafond de ma carte ?','is_final':True}
    assert client.post('/api/transcript-events',json=event).status_code==401
    assert client.post('/api/sessions/'+sid+'/customer',json={'client_id':825},headers={'Authorization':'Bearer transcript-test'}).status_code==401
    auth={'Authorization':'Bearer transcript-test'}
    assert client.post('/api/transcript-events',json=event,headers=auth).json()['disposition']=='accepted'
    initial=settled(client,sid)
    assert initial['resolution']['status']=='candidate' and initial['verified_facts']==[]
    assert client.post('/api/transcript-events',json=event,headers=auth).json()['disposition']=='duplicate'
    assert client.post('/api/sessions/'+sid+'/customer',json={'client_id':825},headers=headers).status_code==200
    assert settled(client,sid)['verified_facts']
    events=client.get('/api/sessions/'+sid+'/events?format=json',headers=headers).json()
    assert any(e['kind']=='answer_produced' for e in events)
    replay=client.get('/api/sessions/'+sid+'/events?format=json',headers={**headers,'Last-Event-ID':str(events[-2]['event_id'])}).json()
    assert len(replay)==1 and replay[0]['event_id']==events[-1]['event_id']


def test_invalid_input_and_unknown_customer(client):
    assert client.post('/api/chat',json={'client_id':825,'message':'   '}).status_code==400
    assert client.post('/api/chat',json={'client_id':825,'message':'a'*8001}).status_code==422
    assert client.post('/api/chat',json={'client_id':999,'message':'Bonjour'}).status_code==404
    assert client.get('/api/clients?limit=-1').status_code==422
    assert client.get('/api/clients/825/monthly?months=999').status_code==422
