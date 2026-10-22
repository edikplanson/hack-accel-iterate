from decimal import Decimal

import pytest

from agent.app.contracts import SessionState,TranscriptTurn,now,SearchResult,IdentityClues
from agent.app.workflow import run_guidance


def state(text='Peut-on augmenter le plafond de sa carte ?',confirmed=True):
    return SessionState(session_id='call-main',selected_client_id=825 if confirmed else None,customer_confirmed=confirmed,
                        turns=[TranscriptTurn(turn_id='1',event_id='1',revision=1,speaker='customer',text=text,is_final=True,timestamp=now())])


def test_main_data_and_bounded_graph(records):
    value=state()
    events=[]
    result=run_guidance(value,lambda *event:events.append(event))
    assert result.status=='needs_clarification'
    assert '2500' in result.text and '[cards#925]' in result.text
    assert 'demo-policy-page-1' in result.text and 'p. 1' in result.text
    assert result.tool_calls_made==['resolve_customer','customer_facts','search_bank_rules']
    assert all(f.data['client_id']==825 for f in value.verified_facts)
    assert all('826' not in s.source_id for s in result.sources)
    assert 'aucun appel' in ' '.join(result.warnings)
    assert len([e for e in events if e[0]=='tool_started'])==3


def test_repository_enforces_confirmed_scope(records):
    from agent.app.tools import get_repository
    with pytest.raises(ValueError):get_repository().retrieve(825,['profile'])
    with pytest.raises(ValueError):get_repository(825).retrieve(826,['profile'])
    with pytest.raises(ValueError):get_repository(825).retrieve(825,['run_readonly_sql'])


def test_conflicting_customer_and_candidate_never_disclose_facts(records):
    value=state('Je suis le client n°826. Je souhaite un nouveau plafond de carte.')
    result=run_guidance(value,lambda *_:None)
    assert result.status=='needs_confirmation' and value.resolution.status=='conflict'
    assert value.verified_facts==[]
    assert not any(s.kind=='database' for s in result.sources)
    value=state('Mon identifiant est 825. Je souhaite un plafond.',confirmed=False)
    run_guidance(value,lambda *_:None)
    assert value.resolution.status=='candidate' and not value.verified_facts


def test_unsupported_identity_and_insurance_are_explicit(records):
    value=state('Je m’appelle Alice Martin. Mon téléphone est 0612345678. Je souhaite un rachat assurance-vie de 1 500 euros.')
    result=run_guidance(value,lambda *_:None)
    assert value.issue_details.declared_amount_eur==Decimal('1500')
    assert any('ni noms ni téléphones' in w for w in result.warnings)
    assert any('pas de contrats' in w for w in result.warnings)
    assert 'Aucun contrat' in result.text
    assert not any('contrats' in (s.table or '') for s in result.sources)


@pytest.mark.parametrize('status',['missing','conflicting','unavailable'])
def test_policy_failures_never_invent_rule(records,monkeypatch,status):
    from agent.app import documents
    monkeypatch.setattr(documents,'get_repository',lambda:type('Docs',(),{'search':lambda *args,**kwargs:SearchResult(status=status)})())
    result=run_guidance(state(),lambda *_:None)
    assert 'demo-policy' not in result.text
    assert ('se contredisent' if status=='conflicting' else 'Procédure non vérifiée') in result.text
    assert (result.status=='error')==(status=='unavailable')


def test_changed_data_is_read_again(records):
    value=state('Quel produit lui proposer ?')
    first=run_guidance(value,lambda *_:None)
    records[825]['yearly_income']=Decimal('51000')
    second=run_guidance(value,lambda *_:None)
    assert '48000' in first.text and '51000' in second.text


def test_transaction_source_uses_transaction_id(records):
    result=run_guidance(state('Je souhaite examiner une transaction.'),lambda *_:None)
    assert any(s.source_id=='transactions#10001' for s in result.sources)


def test_database_failure_does_not_leak_exception(records,monkeypatch):
    from agent.app import data
    def fail(*args):raise RuntimeError('private-connection-string')
    monkeypatch.setattr(data,'client_exists',fail)
    result=run_guidance(state(),lambda *_:None)
    assert result.status=='error' and not any(s.kind=='database' for s in result.sources)
    assert 'private-connection' not in result.model_dump_json()


def test_unrelated_product_document_is_not_presented_as_procedure(records,monkeypatch):
    from agent.app import documents
    from agent.app.contracts import Source
    result=SearchResult(passages=[Source(source_id='unrelated',kind='document',title='Assurance-vie',applicability='Contrats assurance-vie',page=1,passage='Vérifier le rachat.')])
    monkeypatch.setattr(documents,'get_repository',lambda:type('Docs',(),{'search':lambda *args,**kwargs:result})())
    answer=run_guidance(state(),lambda *_:None)
    assert 'Procédure non vérifiée' in answer.text and '[unrelated]' not in answer.text
