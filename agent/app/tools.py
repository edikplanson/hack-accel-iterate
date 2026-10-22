"""Customer specialist for main's existing numeric-ID Kaggle banking schema."""
from . import data
from .contracts import CustomerCandidate, Fact, IdentityClues, Resolution, Source


class CustomerRepository:
    def __init__(self,confirmed_client_id=None):
        self.confirmed_client_id=confirmed_client_id

    def resolve(self, clues: IdentityClues, selected_client_id: int | None = None) -> Resolution:
        client_id=selected_client_id if selected_client_id is not None else clues.client_id
        warnings=[]
        if clues.name or clues.phone:
            warnings.append('Ce jeu de données ne contient ni noms ni téléphones; ces indices ne peuvent pas être vérifiés.')
        if client_id is None:
            return Resolution(status='unresolved',warnings=warnings)
        if selected_client_id is not None and clues.client_id is not None and clues.client_id!=selected_client_id:
            return Resolution(status='conflict',selected_client_id=selected_client_id,warnings=warnings+['L’identifiant déclaré contredit le dossier sélectionné.'])
        if not data.client_exists(client_id):
            return Resolution(status='not_found',warnings=warnings)
        return Resolution(status='confirmed' if selected_client_id is not None else 'candidate',selected_client_id=selected_client_id,
                          candidates=[CustomerCandidate(client_id=client_id,label=f'Client n°{client_id}')],warnings=warnings)

    def retrieve(self, client_id: int, categories: list[str]) -> list[Fact]:
        if not isinstance(client_id,int) or client_id<0 or self.confirmed_client_id is None or client_id!=self.confirmed_client_id:
            raise ValueError('Identifiant client invalide')
        allowed={'profile','cards','spending','transactions','alerts','monthly'}
        if any(category not in allowed for category in categories):
            raise ValueError('Catégorie non autorisée')
        loaders={'profile':lambda:data.client_profile(client_id),'cards':lambda:data.client_cards(client_id),
                 'spending':lambda:data.spending_summary(client_id,3),'transactions':lambda:data.recent_transactions(client_id,10),
                 'alerts':lambda:data.client_alerts(client_id),'monthly':lambda:data.monthly_spending(client_id,3)}
        tables={'profile':'clients','cards':'cards','spending':'transactions','transactions':'transactions','monthly':'transactions','alerts':'computed_alerts'}
        facts=[]
        for category in dict.fromkeys(categories):
            result=loaders[category]()
            if result is None:
                continue
            rows=result if isinstance(result,list) else [result]
            for row in rows:
                if 'error' in row:
                    raise RuntimeError('Données indisponibles')
                if row.get('client_id',client_id)!=client_id:
                    raise RuntimeError('Périmètre client incohérent')
                table=tables[category]
                key=str(row.get('tx_id',client_id) if category=='transactions' else row.get('card_id',client_id))
                source_id=row.get('source') or f'{table}#{key}'
                if category in {'spending','monthly'}:
                    source_id=f'transactions client {client_id}, 3 mois'+(f", {row['mois']}" if category=='monthly' else '')
                facts.append(Fact(category=category,data={**row,'client_id':client_id},sources=[Source(
                    source_id=source_id,kind='database',title=table,table=table,record_id=key)]))
        return facts


def get_repository(confirmed_client_id=None):
    return CustomerRepository(confirmed_client_id)
