"""Requêtes métier utilisées à la fois par l'API (tableau de bord) et par les outils de l'agent."""
import json
from pathlib import Path

from .db import query

MCC_LABELS: dict[str, str] = json.loads(
    (Path(__file__).parent / "mcc_codes.json").read_text(encoding="utf-8")
)


def mcc_label(mcc) -> str:
    return MCC_LABELS.get(str(mcc), f"MCC {mcc}")


def reference_date():
    """Date de la dernière transaction : sert de "aujourd'hui" pour les fenêtres glissantes."""
    rows = query("SELECT max(tx_date) AS ref FROM transactions")
    return rows[0]["ref"] if rows and rows[0]["ref"] else None


def list_clients(search: str | None = None, limit: int = 50) -> list[dict]:
    sql = """
        SELECT c.client_id, c.age, c.gender, c.yearly_income, c.total_debt, c.credit_score,
               count(k.card_id) AS nb_cards,
               bool_or(k.card_on_dark_web) AS card_compromised
        FROM clients c
        LEFT JOIN cards k ON k.client_id = c.client_id
        WHERE (%(s)s::text IS NULL OR c.client_id::text LIKE %(s)s || '%%' OR c.address ILIKE '%%' || %(s)s || '%%')
        GROUP BY c.client_id
        ORDER BY c.client_id
        LIMIT %(limit)s
    """
    return query(sql, {"s": search or None, "limit": limit})


def client_profile(client_id: int) -> dict | None:
    rows = query("SELECT * FROM clients WHERE client_id = %s", (client_id,))
    if not rows:
        return None
    p = rows[0]
    income = float(p["yearly_income"] or 0)
    p["debt_to_income_ratio"] = round(float(p["total_debt"] or 0) / income, 2) if income else None
    return p


def client_exists(client_id: int) -> bool:
    return bool(query('SELECT client_id FROM clients WHERE client_id=%s',(client_id,)))


def client_cards(client_id: int) -> list[dict]:
    ref = reference_date()
    rows = query(
        """
        SELECT client_id, card_id, card_brand, card_type, card_number_masked, expires, has_chip,
               num_cards_issued, credit_limit, acct_open_date, year_pin_last_changed, card_on_dark_web
        FROM cards WHERE client_id = %s ORDER BY card_id
        """,
        (client_id,),
    )
    for r in rows:
        r["expired"] = bool(ref and r["expires"] and r["expires"] < ref.date())
    return rows


def spending_summary(client_id: int, months: int = 3) -> dict:
    ref = reference_date()
    if ref is None:
        return {"error": "aucune transaction en base"}
    params = {"cid": client_id, "ref": ref, "months": f"{int(months)} months"}
    window = "client_id = %(cid)s AND tx_date > %(ref)s::timestamp - %(months)s::interval"

    totals = query(
        f"""
        SELECT count(*) AS nb_transactions,
               coalesce(sum(amount) FILTER (WHERE amount > 0), 0) AS total_depenses,
               coalesce(-sum(amount) FILTER (WHERE amount < 0), 0) AS total_remboursements,
               count(*) FILTER (WHERE errors IS NOT NULL) AS nb_incidents,
               coalesce(max(amount), 0) AS plus_grosse_depense
        FROM transactions WHERE {window}
        """,
        params,
    )[0]

    by_category = query(
        f"""
        SELECT mcc, sum(amount) AS total, count(*) AS nb
        FROM transactions WHERE {window} AND amount > 0
        GROUP BY mcc ORDER BY total DESC LIMIT 8
        """,
        params,
    )
    for row in by_category:
        row["categorie"] = mcc_label(row.pop("mcc"))

    incidents = query(
        f"""
        SELECT errors, count(*) AS nb, max(tx_date) AS dernier
        FROM transactions WHERE {window} AND errors IS NOT NULL
        GROUP BY errors ORDER BY nb DESC
        """,
        params,
    )

    return {
        "periode_mois": months,
        "date_reference": ref,
        **totals,
        "par_categorie": by_category,
        "incidents": incidents,
    }


def monthly_spending(client_id: int, months: int = 12) -> list[dict]:
    ref = reference_date()
    if ref is None:
        return []
    return query(
        """
        SELECT to_char(date_trunc('month', tx_date), 'YYYY-MM') AS mois,
               coalesce(sum(amount) FILTER (WHERE amount > 0), 0) AS depenses,
               count(*) AS nb_transactions
        FROM transactions
        WHERE client_id = %(cid)s AND tx_date > %(ref)s::timestamp - %(months)s::interval
        GROUP BY 1 ORDER BY 1
        """,
        {"cid": client_id, "ref": ref, "months": f"{int(months)} months"},
    )


def recent_transactions(client_id: int, limit: int = 20, only_incidents: bool = False) -> list[dict]:
    rows = query(
        """
        SELECT client_id, tx_id, tx_date, amount, use_chip, merchant_city, merchant_state, mcc, errors, card_id
        FROM transactions
        WHERE client_id = %(cid)s AND (NOT %(inc)s OR errors IS NOT NULL)
        ORDER BY tx_date DESC LIMIT %(limit)s
        """,
        {"cid": client_id, "inc": only_incidents, "limit": min(int(limit), 100)},
    )
    for r in rows:
        r["categorie"] = mcc_label(r["mcc"])
    return rows


def client_alerts(client_id: int) -> list[dict]:
    """Alertes calculées à partir des données (règles simples, explicables)."""
    alerts = []
    for card in client_cards(client_id):
        if card["card_on_dark_web"]:
            alerts.append({"niveau": "critique", "type": "carte_compromise", "source": f"cards#{card['card_id']}",
                           "message": f"Carte {card['card_number_masked']} signalée compromise"})
        elif card["expired"]:
            alerts.append({"niveau": "info", "type": "carte_expiree", "source": f"cards#{card['card_id']}",
                           "message": f"Carte {card['card_number_masked']} expirée, renouvellement à proposer"})

    summary = spending_summary(client_id, months=1)
    for inc in summary.get("incidents", []):
        if "Insufficient Balance" in (inc["errors"] or ""):
            alerts.append({"niveau": "attention", "type": "solde_insuffisant", "source": f"transactions client {client_id}",
                           "message": f"{inc['nb']} paiement(s) refusé(s) pour solde insuffisant sur 30 jours"})

    profile = client_profile(client_id)
    if profile and profile["credit_score"] is not None and profile["credit_score"] < 600:
        alerts.append({"niveau": "attention", "type": "score_faible", "source": f"clients#{client_id}",
                       "message": f"Score de crédit faible ({profile['credit_score']})"})
    return alerts


def dashboard(client_id: int) -> dict | None:
    profile = client_profile(client_id)
    if not profile:
        return None
    s30 = spending_summary(client_id, months=1)
    return {
        "profil": profile,
        "cartes": client_cards(client_id),
        "kpis": {
            "depenses_30j": s30.get("total_depenses"),
            "transactions_30j": s30.get("nb_transactions"),
            "incidents_30j": s30.get("nb_incidents"),
            "date_reference": s30.get("date_reference"),
        },
        "categories_30j": s30.get("par_categorie", []),
        "alertes": client_alerts(client_id),
        "dernieres_transactions": recent_transactions(client_id, limit=10),
    }
