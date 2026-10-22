"""Outils exposés à l'agent LangGraph. Chaque outil renvoie du JSON avec ses sources."""
import re

from langchain_core.tools import tool

from . import data
from .db import query, query_readonly, to_json


@tool
def get_client_profile(client_id: int) -> str:
    """Profil du client : âge, revenus annuels, dette totale, ratio dette/revenu, score de crédit, nombre de cartes."""
    p = data.client_profile(client_id)
    if not p:
        return to_json({"error": f"client {client_id} introuvable"})
    return to_json({"source": f"clients#{client_id}", "profil": p})


@tool
def get_cards(client_id: int) -> str:
    """Cartes bancaires du client : marque, type, plafond, expiration, puce, signalement compromission."""
    return to_json({"source": f"cards (client {client_id})", "cartes": data.client_cards(client_id)})


@tool
def get_spending_summary(client_id: int, months: int = 3) -> str:
    """Synthèse des dépenses sur N mois : total, remboursements, top catégories, incidents de paiement."""
    s = data.spending_summary(client_id, months)
    return to_json({"source": f"transactions (client {client_id}, {months} mois)", **s})


@tool
def get_recent_transactions(client_id: int, limit: int = 20, only_incidents: bool = False) -> str:
    """Dernières transactions du client. only_incidents=True pour ne voir que les paiements en erreur."""
    rows = data.recent_transactions(client_id, limit, only_incidents)
    return to_json({"source": f"transactions (client {client_id})", "transactions": rows})


@tool
def get_client_alerts(client_id: int) -> str:
    """Alertes actives du client : carte compromise, carte expirée, refus pour solde insuffisant, score faible."""
    return to_json({"alertes": data.client_alerts(client_id)})


@tool
def search_bank_rules(question: str, k: int = 5) -> str:
    """Recherche les clauses pertinentes dans les règles internes de la banque (PDF indexés)."""
    try:
        count = query("SELECT count(*) AS n FROM policy_chunks")[0]["n"]
    except Exception as exc:  # table absente
        return to_json({"error": f"règles indisponibles : {exc}"})
    if count == 0:
        return to_json({"error": "aucune règle interne indexée pour le moment"})
    # Recherche plein texte PostgreSQL (français), termes combinés en OU et classés par pertinence
    rows = query(
        """
        WITH q AS (
            SELECT NULLIF(replace(plainto_tsquery('french', %(q)s)::text, '&', '|'), '')::tsquery AS tsq
        )
        SELECT doc_name, section, page, content,
               ts_rank(to_tsvector('french', content), q.tsq) AS score
        FROM policy_chunks, q
        WHERE to_tsvector('french', content) @@ q.tsq
        ORDER BY score DESC LIMIT %(k)s
        """,
        {"q": question, "k": min(int(k), 10)},
    )
    for r in rows:
        r["source"] = f"{r['doc_name']} §{r['section']}"
    return to_json({"clauses": rows})


_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do)\b", re.I)


@tool
def run_readonly_sql(sql: str) -> str:
    """Requête SQL libre en lecture seule (SELECT ou WITH) sur les tables clients, cards, transactions.
    À utiliser seulement si aucun autre outil ne répond à la question. Maximum 200 lignes."""
    clean = sql.strip().rstrip(";")
    if ";" in clean or not re.match(r"^(select|with)\b", clean, re.I) or _FORBIDDEN.search(clean):
        return to_json({"error": "seules les requêtes SELECT uniques sont autorisées"})
    try:
        rows = query_readonly(clean)
    except Exception as exc:
        return to_json({"error": str(exc)})
    return to_json({"source": "requête SQL", "sql": clean, "lignes": rows})


TOOLS = [
    get_client_profile,
    get_cards,
    get_spending_summary,
    get_recent_transactions,
    get_client_alerts,
    search_bank_rules,
    run_readonly_sql,
]
