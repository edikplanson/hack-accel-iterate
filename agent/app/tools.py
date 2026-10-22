"""Outils exposés à l'agent LangGraph. Chaque outil renvoie du JSON avec ses sources."""
import re
from typing import Literal

from langchain_core.tools import tool

from . import data, rules
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
def search_bank_rules(question: str, k: int = 5, doc_code: str | None = None) -> str:
    """Recherche les articles pertinents dans les règles internes de la banque (PDF indexés dans OpenSearch).
    Les règles sont rédigées en anglais : formule question en anglais avec des mots-clés précis
    (ex. "overdraft authorised limit fees", "card blocking stolen card", "credit debt-to-income ratio").
    doc_code restreint à un document : REF (cadre commun), CARD (plafonds cartes), BLK (opposition, contestations),
    OVD (découvert, frais d'incident), LOAN (éligibilité crédit), KYC (connaissance client, LCB-FT),
    SAV (épargne), FRD (surveillance, anti-fraude). Chaque article a une source à citer et des renvois (refs)."""
    try:
        return to_json({"clauses": rules.search(question, max(1, min(int(k), 10)), doc_code)})
    except Exception as exc:
        return to_json({"error": f"règles indisponibles : {exc}"})


@tool
def get_bank_rule(article_ids: list[str]) -> str:
    """Texte complet d'articles des règles internes par identifiant (ex. ["CARD-2.1", "OVD-3.2"]).
    À utiliser pour suivre les renvois (refs) d'un article trouvé avec search_bank_rules."""
    try:
        return to_json({"clauses": rules.get_articles([str(a).upper() for a in article_ids[:10]])})
    except Exception as exc:
        return to_json({"error": f"règles indisponibles : {exc}"})


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


# ---------- Outils écran : ils ne lisent pas la base, ils décrivent un changement d'affichage ----------

Level = Literal["critique", "attention", "info", "positif"]
Section = Literal["revenu", "dette", "score", "depenses", "alertes", "cartes", "categories", "mensuel", "transactions"]


def _ui(payload: dict) -> str:
    return to_json({"ok": True, "ui": payload})


@tool
def ui_show_chart(
    title: str,
    labels: list[str],
    values: list[float],
    chart_type: Literal["bar", "line", "pie"] = "bar",
    series_label: str = "",
    note: str = "",
) -> str:
    """Affiche un graphique sur l'écran du conseiller.
    À utiliser dès qu'un résultat chiffré se compare (catégories, cartes, clients) ou évolue dans le temps (mois).
    labels et values doivent avoir la même longueur (24 points maximum) et venir d'un outil de données.
    chart_type : "bar" pour comparer, "line" pour une évolution, "pie" pour une répartition.
    note : une phrase courte qui dit ce qu'il faut retenir du graphique."""
    n = min(len(labels), len(values), 24)
    return _ui({
        "action": "chart",
        "title": title,
        "chart_type": chart_type,
        "labels": [str(x) for x in labels[:n]],
        "values": [float(v) for v in values[:n]],
        "series_label": series_label,
        "note": note,
    })


@tool
def ui_highlight(section: Section, message: str, level: Level = "attention") -> str:
    """Encadre une zone déjà affichée sur le tableau de bord et y ajoute une note.
    À utiliser dès que ton raisonnement s'appuie sur une de ces zones :
    revenu, dette, score (score de crédit), depenses (dépenses 30 jours), alertes, cartes,
    categories (top dépenses 30 jours), mensuel (graphique des dépenses mensuelles), transactions (dernières opérations).
    message : une phrase courte expliquant pourquoi la zone compte. level : critique, attention, info ou positif."""
    return _ui({"action": "highlight", "section": section, "message": message, "level": level})


@tool
def ui_show_table(title: str, columns: list[str], rows: list[list[str | int | float | None]], note: str = "") -> str:
    """Affiche un tableau sur l'écran du conseiller.
    À utiliser pour une liste de lignes : transactions suspectes, incidents de paiement, résultat d'une requête SQL.
    Chaque ligne a autant de cellules que columns. 20 lignes maximum, valeurs issues des outils de données."""
    clean = [["" if c is None else str(c) for c in r] for r in rows[:20]]
    return _ui({"action": "table", "title": title, "columns": [str(c) for c in columns], "rows": clean, "note": note})


@tool
def ui_show_metric(label: str, value: str, detail: str = "", level: Level = "info") -> str:
    """Affiche un chiffre clé en grand sur l'écran du conseiller.
    À utiliser pour un indicateur que tu as calculé ou extrait (taux d'endettement, hausse des dépenses, nombre d'incidents).
    value est déjà formatée (ex. "2 340 €", "+18 %"). detail : contexte en une phrase. level : critique, attention, info ou positif."""
    return _ui({"action": "metric", "label": label, "value": value, "detail": detail, "level": level})


TOOLS = [
    get_client_profile,
    get_cards,
    get_spending_summary,
    get_recent_transactions,
    get_client_alerts,
    search_bank_rules,
    get_bank_rule,
    run_readonly_sql,
    ui_show_chart,
    ui_highlight,
    ui_show_table,
    ui_show_metric,
]
