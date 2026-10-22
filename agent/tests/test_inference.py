from decimal import Decimal
from types import SimpleNamespace

import pytest

from agent.app.contracts import SessionState,Resolution,Fact,Source
from agent.app.inference import Limits,InferenceError,synthesize


class Ledger:
    def __init__(self):self.events=[]
    def reserve(self,state,limits,amount):
        self.events.append(('reserve',amount))
        return 'test-reservation'
    def finish(self,*args):self.events.append(('finish',args))


def state():
    return SessionState(session_id='model-test',selected_client_id=825,customer_confirmed=True,
                        resolution=Resolution(status='confirmed'),verified_facts=[Fact(category='profile',data={'client_id':825,'yearly_income':48000},
                        sources=[Source(source_id='clients#825',kind='database',title='clients')])])


def test_disabled_inference_never_uses_even_a_configured_key(monkeypatch):
    monkeypatch.setenv('MODEL_ENABLED','false')
    monkeypatch.setenv('ANTHROPIC_API_KEY','fake-test-key')
    with pytest.raises(InferenceError,match='désactivée'):
        synthesize(state(),'Données',client=object(),ledger=object())


def test_opt_in_client_uses_configured_model_and_reserves_first(monkeypatch):
    monkeypatch.setenv('MODEL_ENABLED','true')
    monkeypatch.setenv('ANTHROPIC_MODEL','claude-sonnet-5-5')
    ledger=Ledger()
    def models(**kwargs):
        assert ledger.events[0][0]=='reserve'
        return SimpleNamespace(data=[SimpleNamespace(id='claude-sonnet-5-5')])
    def message(**kwargs):
        assert 'temperature' not in kwargs  # Sonnet 5.5 rejects sampling parameters.
        assert kwargs['model']=='claude-sonnet-5-5' and kwargs['max_tokens']<=4096
        return SimpleNamespace(content=[SimpleNamespace(type='text',text='Revenu annuel : 48000 euros [clients#825].')],usage=SimpleNamespace(input_tokens=100,output_tokens=20))
    client=SimpleNamespace(models=SimpleNamespace(list=models),messages=SimpleNamespace(create=message))
    assert '[clients#825]' in synthesize(state(),'Revenu',client=client,ledger=ledger)
    assert ledger.events[-1][0]=='finish' and ledger.events[-1][1][-2:]==(100,20)


@pytest.mark.parametrize('amount',['301','NaN','0','-1'])
def test_budget_ceiling_cannot_exceed_overall_limit(monkeypatch,amount):
    monkeypatch.setenv('MODEL_BUDGET_USD',amount)
    with pytest.raises(InferenceError):Limits.from_env()


def test_default_prices_match_main_model(monkeypatch):
    monkeypatch.delenv('MODEL_BUDGET_USD',raising=False)
    limits=Limits.from_env()
    assert limits.budget==Decimal('5')
    assert limits.input_rate==Decimal('2') and limits.output_rate==Decimal('10')


def test_failed_request_remains_uncertain_and_never_retries(monkeypatch):
    monkeypatch.setenv('MODEL_ENABLED','true')
    calls=[]
    ledger=Ledger()
    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError('private-provider-error')
    client=SimpleNamespace(models=SimpleNamespace(list=lambda **kwargs:SimpleNamespace(data=[SimpleNamespace(id='claude-sonnet-5-5')])),messages=SimpleNamespace(create=fail))
    with pytest.raises(InferenceError) as exc:
        synthesize(state(),'Revenu',client=client,ledger=ledger)
    assert len(calls)==1 and 'private-provider' not in str(exc.value)
    assert ledger.events[-1][1][-2:]==(None,None)
