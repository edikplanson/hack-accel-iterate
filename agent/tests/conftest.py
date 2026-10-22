"""Clearly synthetic main-schema fixtures; no managed services or inference."""
from decimal import Decimal

import pytest


@pytest.fixture
def records(monkeypatch):
    from agent.app import data,documents
    from agent.app.contracts import SearchResult,Source
    monkeypatch.setenv('APP_MODE','mock')
    monkeypatch.setenv('MODEL_ENABLED','false')
    monkeypatch.setenv('ADVISOR_API_TOKEN','advisor-test')
    monkeypatch.setenv('TRANSCRIPT_TOKEN','transcript-test')
    profiles={825:{'client_id':825,'yearly_income':Decimal('48000'),'total_debt':Decimal('5000'),'credit_score':680},
              826:{'client_id':826,'yearly_income':Decimal('90000'),'total_debt':Decimal('20000'),'credit_score':720}}
    monkeypatch.setattr(data,'client_exists',lambda cid:cid in profiles)
    monkeypatch.setattr(data,'client_profile',lambda cid:profiles.get(cid))
    monkeypatch.setattr(data,'client_cards',lambda cid:[{'client_id':cid,'card_id':cid+100,'card_number_masked':'**** 1234','credit_limit':Decimal('2500'),'card_on_dark_web':False}])
    monkeypatch.setattr(data,'spending_summary',lambda cid,months=3:{'total_depenses':Decimal('1300'),'nb_transactions':12,'nb_incidents':1})
    monkeypatch.setattr(data,'recent_transactions',lambda cid,limit=20,only_incidents=False:[{'client_id':cid,'tx_id':10001,'card_id':cid+100,'amount':Decimal('70.25'),'errors':'Insufficient Balance'}])
    monkeypatch.setattr(data,'client_alerts',lambda cid:[{'source':f'cards#{cid+100}','message':'Point à vérifier','niveau':'info'}])
    monkeypatch.setattr(data,'monthly_spending',lambda cid,months=3:[{'mois':'2026-10','depenses':Decimal('1300')}])
    monkeypatch.setattr(documents,'get_repository',lambda:type('Docs',(),{'search':lambda self,*args,**kwargs:SearchResult(passages=[Source(source_id='demo-policy-page-1',kind='document',title='Procédure FICTIVE',page=1,version='demo-1',passage='Vérifier les conditions et demander la validation du gestionnaire.',fictional=True)])})())
    return profiles
