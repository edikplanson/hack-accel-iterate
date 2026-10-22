"""Issue-driven specialist routing over main's existing data contracts."""
import os
import re
import unicodedata
from decimal import Decimal

from .contracts import ChatResponse, IdentityClues, IssueDetails, Resolution, SearchResult


def normal(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(c))


def plan(text):
    text=normal(text)
    if any(w in text for w in ('rachat','assurance-vie','assurance vie','retrait')):
        return 'Demande de retrait / assurance-vie',['profile'],'rachat retrait assurance-vie procedure',[
            'Quel contrat et quel type de retrait sont concernés ?', 'À quelle échéance souhaitez-vous disposer des fonds ?']
    if any(w in text for w in ('fiche','rendez-vous','rendez vous','reunion','risque','vigilance')):
        return 'Préparation du dossier conseiller',['profile','cards','spending','alerts'],None,[
            'Quel besoin souhaitez-vous prioriser pendant le rendez-vous ?']
    if any(w in text for w in ('carte','plafond','opposition','compromis','fraude')):
        return 'Demande concernant une carte',['profile','cards','alerts'],'carte plafond opposition procedure',[
            'Quelle carte est concernée et quelle démarche souhaitez-vous préparer ?']
    if any(w in text for w in ('depens','transaction','paiement','virement','operation')):
        query='paiement contestation incident procedure' if any(w in text for w in ('refus','incident','conteste','autorise')) else None
        return 'Analyse des opérations',['spending','transactions'],query,[
            'Quelle période ou quelle opération souhaitez-vous examiner ?']
    if any(w in text for w in ('credit','pret','dette','produit')):
        return 'Qualification du besoin financier',['profile','spending'],'credit demande justificatifs procedure',[
            'Quel est le besoin, le montant et l’horizon du projet ?']
    return 'Motif à préciser',['profile'],None,['Quelle demande souhaitez-vous traiter pour ce client ?']


def prepare(value):
    state=value['session']
    turns=[t for t in state.turns if t.is_final and t.speaker!='system'][-20:]
    state.customer_statements=[t.text for t in turns if t.speaker=='customer']
    conversation='\n'.join(t.text for t in turns)[-24000:]
    identity='\n'.join(state.customer_statements) if state.customer_statements else conversation
    ids=re.findall(r'\b(?:client|identifiant)(?:\s*(?:n[°o]?|numero|:|est))?\s*#?\s*(\d+)\b',normal(identity))
    names=re.findall(r"je m['’]appelle\s+([A-Za-zÀ-ÿ'-]+(?:\s+[A-Za-zÀ-ÿ'-]+)?)",identity,re.I)
    phones=re.findall(r'(?:\+33|0)[1-9](?:[ .-]?\d{2}){4}',identity)
    state.identity_clues=IdentityClues(client_id=int(ids[-1]) if ids else None,name=names[-1] if names else None,phone=phones[-1] if phones else None)
    amounts=re.findall(r'(?<!\d)(\d[\d \u00a0\u202f]*(?:[,.]\d{1,2})?)\s*(?:€|euros?\b)',conversation,re.I)
    types=re.findall(r'\b(partiel|total)\b',normal(conversation))
    state.issue_details=IssueDetails(declared_amount_eur=Decimal(re.sub(r'\s','',amounts[-1]).replace(',','.')) if amounts else None,
                                     withdrawal_type=types[-1] if types else None)
    choice=plan(conversation)
    for turn in reversed(turns):
        if plan(turn.text)[0]!='Motif à préciser':
            choice=plan(turn.text)
            break
    state.issue_summary=choice[0]
    state.missing_information=choice[3]
    if state.issue_details.declared_amount_eur is None and 'retrait' in state.issue_summary.lower():
        state.missing_information.append('Quel montant souhaitez-vous retirer ?')
    state.verified_facts=[]
    state.documentation=SearchResult(status='missing')
    state.errors=[]
    return {'session':state,'categories':choice[1],'policy_query':choice[2],'calls':[],'warnings':[]}


def retrieve(value):
    from .tools import get_repository
    from .documents import get_repository as documents
    state=value['session']
    emit=value['emit']
    calls=[]
    warnings=[]
    def tool(name,action):
        if len(calls)>=3:
            raise RuntimeError('Limite de consultations atteinte')
        calls.append(name)
        emit('tool_started',name,'Consultation en lecture seule, périmètre borné')
        try:
            result=action()
        except Exception:
            emit('tool_failed',name,'Consultation indisponible')
            raise
        emit('tool_completed',name,'Consultation terminée')
        return result
    try:
        repo=get_repository(state.selected_client_id if state.customer_confirmed else None)
        state.resolution=tool('resolve_customer',lambda:repo.resolve(state.identity_clues,state.selected_client_id))
        warnings.extend(state.resolution.warnings)
        if state.customer_confirmed and state.selected_client_id is not None and state.resolution.status=='confirmed':
            state.verified_facts=tool('customer_facts',lambda:repo.retrieve(state.selected_client_id,value['categories']))
            emit('data_retrieved','customer_facts',f'{len(state.verified_facts)} faits vérifiés')
        else:
            state.missing_information.insert(0,'Le conseiller peut-il vérifier l’identité et confirmer le dossier ?')
    except Exception:
        state.resolution=Resolution(status='unresolved')
        state.verified_facts=[]
        warnings.append('PostgreSQL indisponible : aucun fait individuel vérifié.')
        state.errors.append('customer_retrieval_failed')
    if value.get('policy_query'):
        try:
            state.documentation=tool('search_bank_rules',lambda:documents().search(value['policy_query'],limit=3))
            emit('data_retrieved','search_bank_rules',f'{len(state.documentation.passages)} passages retrouvés')
        except Exception:
            state.documentation=SearchResult(status='unavailable')
        warnings.extend(state.documentation.warnings)
    return {'session':state,'calls':calls,'warnings':warnings}


def fact_lines(facts):
    labels={'yearly_income':'Revenu annuel (€)','total_debt':'Dette totale (€)','credit_score':'Score de crédit',
            'card_number_masked':'Carte','credit_limit':'Plafond (€)','card_on_dark_web':'Signalement de compromission',
            'total_depenses':'Dépenses (€)','nb_transactions':'Opérations','nb_incidents':'Incidents',
            'tx_id':'Opération','amount':'Montant (€)','errors':'Incident','message':'Alerte'}
    lines=[]
    for fact in facts:
        details=[f'{label} : {fact.data[k]}' for k,label in labels.items() if fact.data.get(k) is not None]
        if details:
            lines.append('• '+' ; '.join(details[:3])+f' [{fact.sources[0].source_id}]')
        if len(lines)==4:
            break
    return lines


def compose(value):
    state=value['session']
    warnings=list(value['warnings'])
    lines=[state.issue_summary+'.']
    if state.resolution.status=='conflict':
        lines.append('L’identifiant déclaré contredit le dossier sélectionné. Vérifiez le client avant toute consultation.')
    elif not state.customer_confirmed or state.resolution.status!='confirmed':
        lines.append('Aucun fait individuel affiché avant confirmation du dossier par le conseiller.')
    else:
        lines.extend(fact_lines(state.verified_facts))
    if 'assurance-vie' in state.issue_summary:
        warnings.append('La base actuelle ne contient pas de contrats d’assurance-vie; ne pas inventer un encours ou une ancienneté fiscale.')
        lines.append('Aucun contrat d’assurance-vie disponible dans ce jeu de données. Consultez le dossier contractuel.')
    if value.get('policy_query'):
        docs=state.documentation
        if docs.status=='ok' and docs.passages:
            source=docs.passages[0]
            label='Document FICTIF' if source.fictional else 'Passage à vérifier'
            sentences=re.split(r'(?<=[.!?])\s+',' '.join((source.passage or '').split()))
            useful=[s for s in sentences if re.search(r'\b(verifier|demander|orienter|confirmer)\b',normal(s))]
            excerpt=' '.join(useful[:2])[:280] or (source.passage or '')[:280]
            lines.append(f'{label} : « {excerpt} » [{source.source_id}] (p. {source.page}). Applicabilité au dossier à confirmer.')
        elif docs.status=='conflicting':
            lines.append('Les procédures retrouvées se contredisent; demander une validation humaine.')
        else:
            lines.append('Procédure non vérifiée : ne promettez ni autorisation, délai ou tarif sans document applicable.')
            state.missing_information.append('Quelle procédure interne validée couvre cette demande ?')
    lines.extend('? '+q for q in state.missing_information[:3])
    text='\n'.join(lines)
    model_failed=False
    if os.getenv('MODEL_ENABLED','false').lower()=='true' and state.resolution.status=='confirmed' and not state.errors and (not value.get('policy_query') or state.documentation.status=='ok'):
        value['calls'].append('claude_synthesis')
        try:
            from .inference import synthesize
            value['emit']('tool_started','claude_synthesis','Synthèse bornée des preuves')
            text=synthesize(state,text)
            value['emit']('tool_completed','claude_synthesis','Synthèse terminée')
        except Exception:
            model_failed=True
            warnings.append('Synthèse Claude échouée; restitution déterministe des preuves conservée.')
            value['emit']('tool_failed','claude_synthesis','Synthèse indisponible')
    elif os.getenv('MODEL_ENABLED','false').lower()!='true':
        warnings.append('Synthèse Claude désactivée; aucun appel d’inférence.')
    sources={s.source_id:s for f in state.verified_facts for s in f.sources}
    sources.update({s.source_id:s for s in state.documentation.passages})
    status='error' if state.errors or model_failed or state.documentation.status=='unavailable' else 'needs_confirmation' if state.resolution.status!='confirmed' or not state.customer_confirmed else 'needs_clarification'
    response=ChatResponse(text=text,session_id=state.session_id,trace_id=state.trace_id,status=status,
                          issue_summary=state.issue_summary,clarification_questions=state.missing_information,
                          sources=list(sources.values()),warnings=warnings,tool_calls_made=value['calls'])
    return {'response':response}


def run_guidance(state,emit):
    from .graph import graph
    return graph().invoke({'session':state,'emit':emit},config={'recursion_limit':6})['response']
