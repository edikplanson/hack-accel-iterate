"""Opt-in SQL compatibility check; explicitly refuses managed/other databases."""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark=pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='Dedicated local TEST_DATABASE_URL required')


def test_main_schema_api_and_fresh_data(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    import psycopg
    url=os.environ['TEST_DATABASE_URL']
    options=conninfo_to_dict(url)
    assert options.get('host') in {'localhost','127.0.0.1'} and options.get('dbname')=='copilot_main_agent_test'
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('ALLOW_INSECURE_LOCAL','true')
    monkeypatch.setenv('APP_MODE','live')
    monkeypatch.setenv('MODEL_ENABLED','false')
    from agent.app import db,documents,main
    from agent.app.contracts import SearchResult
    from agent.app.sessions import SessionStore
    from agent.app.migrate import migrate
    with psycopg.connect(url,autocommit=True) as conn:
        conn.execute(Path(__file__).with_name('main_schema.sql').read_text())
    previous=db._pool
    monkeypatch.setattr(db,'_pool',None)
    migrate()
    monkeypatch.setattr(main,'store',SessionStore())
    # Marked missing-document fixture: this run verifies real PostgreSQL, not OpenSearch.
    monkeypatch.setattr(documents,'get_repository',lambda:type('Docs',(),{'search':lambda *args,**kwargs:SearchResult(status='missing')})())
    try:
        with TestClient(main.app) as client:
            assert client.get('/health').json()['db'] is True
            assert len(client.get('/api/clients').json())==2
            dashboard=client.get('/api/clients/825').json()
            assert dashboard['profil']['client_id']==825 and dashboard['cartes'][0]['card_id']==925
            assert dashboard['kpis']['depenses_30j']==170.25
            assert all(t['client_id']==825 for t in dashboard['dernieres_transactions'])
            assert client.get('/api/clients/825/monthly').json()
            first=client.post('/api/chat',json={'client_id':825,'message':'Quels sont les risques sur ce client ?'}).json()
            assert '48000' in first['answer'] and first['status']!='error'
            sid=first['thread_id']
            with db.pool().connection() as conn:
                conn.execute('UPDATE clients SET yearly_income=51000 WHERE client_id=825')
            try:
                second=client.post('/api/chat',json={'client_id':825,'message':'Et maintenant ?','thread_id':sid}).json()
                assert '51000' in second['answer']
                assert len(SessionStore().get(sid).turns)==2
                assert client.post('/api/chat',json={'client_id':826,'message':'Bonjour','thread_id':sid}).status_code==409
            finally:
                with db.pool().connection() as conn:
                    conn.execute('UPDATE clients SET yearly_income=48000 WHERE client_id=825')
        # A fresh store observes the saved guidance after API lifespan/connection-pool replacement.
        saved=SessionStore().get(sid)
        assert '51000' in saved.guidance.text and saved.processed_revision==2
    finally:
        if db._pool is not None:db._pool.close()
        db._pool=previous
