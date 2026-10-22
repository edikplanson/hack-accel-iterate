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

Règles :
- Appuie chaque affirmation sur les outils : profil, cartes, dépenses, transactions, alertes, règles internes.
- Pour une question sur ce qui est autorisé ou non (plafond, découvert, crédit, opposition), cherche la règle avec search_bank_rules et croise-la avec les données du client.
- Cite tes sources entre crochets à la fin de la phrase concernée, par exemple [clients#825], [cards#4524], [transactions client 825, 3 mois] ou [Politique cartes §3.1].
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
