"""Graphe LangGraph : un agent qui raisonne, appelle les outils de données, puis répond en citant ses sources."""
import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .data import reference_date
from .tools import TOOLS

BANK_NAME = os.environ.get("BANK_NAME", "Banque Horizon")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

SYSTEM_PROMPT = """Tu es le copilote des conseillers bancaires de {bank}.
Le conseiller travaille sur le client n°{client_id}. Date de référence des données : {ref}.

Tu disposes de deux sources de données distinctes :
1. Les données clients (base PostgreSQL) : profil, cartes, transactions, alertes. Outils get_client_profile, get_cards, get_spending_summary, get_recent_transactions, get_client_alerts, run_readonly_sql. Elles décrivent la situation du client.
2. Les règles de l'agence (base OpenSearch) : le règlement interne que le conseiller doit appliquer, issu des PDF officiels de la banque (cadre commun, plafonds et fonctionnement des cartes, opposition et contestations, découvert et frais d'incident, éligibilité au crédit, connaissance client et LCB-FT, épargne, surveillance anti-fraude). Outils search_bank_rules et get_bank_rule. Ces documents sont rédigés en anglais et le nom d'établissement qui y figure peut différer : ce sont bien les règles de notre agence et elles font foi. Elles disent ce qui est autorisé, interdit, plafonné ou facturé, et la procédure à suivre.
Ne confonds jamais les deux : un chiffre client vient de PostgreSQL, une limite, un seuil, un frais ou une procédure vient des règles.

Règles :
- Appuie chaque affirmation sur les outils : données clients pour les faits, règles de l'agence pour ce qui est permis ou exigé.
- Dès qu'une question touche à ce qui est autorisé, à un plafond, un découvert, un crédit, une opposition, un frais, une vérification KYC ou un soupçon de fraude, consulte d'abord les règles avec search_bank_rules (requête en mots-clés anglais), suis leurs renvois (refs) avec get_bank_rule si besoin, puis compare la règle aux données du client. Ne réponds jamais de mémoire sur une règle : si aucun article ne correspond, dis-le. Traduis en français ce que tu en cites.
- Cite tes sources entre crochets à la fin de la phrase concernée, par exemple [clients#825], [cards#4524], [transactions client 825, 3 mois] ou [Card Limits and Card Operation §CARD-2.1].
- Si une donnée manque, dis-le clairement au lieu d'inventer.
- Les montants sont en euros. Réponds en français, de façon concise et orientée action pour le conseiller : constat, risque ou opportunité, action proposée.
- N'utilise jamais de tiret long ou double dans tes réponses.

Tu pilotes aussi l'écran du conseiller en temps réel avec les outils ui_*. Utilise ui_highlight dès que tu t'appuies sur une zone déjà affichée (revenu, dette, score, depenses, alertes, cartes, categories, mensuel, transactions). Utilise ui_show_chart quand un résultat chiffré se compare ou évolue, ui_show_table pour une liste de lignes (transactions suspectes, résultat SQL), ui_show_metric pour un chiffre clé que tu as calculé. Appelle-les au fil de l'analyse, juste après avoir obtenu les données, jamais tous à la fin. 1 à 3 éléments visuels par réponse. N'invente aucune valeur : uniquement des chiffres issus des outils de données. Ne recopie pas dans ton texte un tableau déjà affiché, renvoie le conseiller vers l'écran."""


def _system(client_id: int | None) -> SystemMessage:
    ref = reference_date()
    return SystemMessage(
        SYSTEM_PROMPT.format(
            bank=BANK_NAME,
            client_id=client_id if client_id is not None else "non précisé",
            ref=ref.date().isoformat() if ref else "inconnue",
        )
    )


class State(MessagesState):
    client_id: int | None


def build_graph():
    # Pas de temperature : Sonnet 5.5 refuse les valeurs non par défaut (400). La réflexion adaptative,
    # active par défaut, consomme aussi max_tokens : on laisse de la marge pour ne pas tronquer la réponse.
    llm = ChatAnthropic(model=MODEL, max_tokens=8192).bind_tools(TOOLS)

    def agent(state: State):
        messages = [_system(state.get("client_id"))] + state["messages"]
        return {"messages": [llm.invoke(messages)]}

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    # Mémoire de conversation par thread (en RAM : suffisant pour la démo)
    return g.compile(checkpointer=InMemorySaver())


_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
