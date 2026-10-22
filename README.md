# Horizon Copilote

Copilote IA pour conseillers bancaires (banque fictive Banque Horizon), projet du hackathon Accel AI Innovate Paris.

```
Navigateur -> frontend (nginx, page statique) -> agent (FastAPI + LangGraph) -> Aiven PostgreSQL + pgvector
```

## Structure

| Dossier | Rôle |
|---|---|
| `frontend/` | Interface conseiller : liste clients, vue 360, alertes, chat avec sources. nginx relaie `/api` vers l'agent. |
| `agent/` | API FastAPI et graphe LangGraph. L'agent appelle des outils SQL (profil, cartes, dépenses, transactions, alertes), la recherche dans les règles internes (pgvector) et un SQL libre en lecture seule. |
| `seed-bdd/` | Chargement du dataset Kaggle dans PostgreSQL. |

## Lancer en local

```bash
cp .env.example .env   # remplir DATABASE_URL et ANTHROPIC_API_KEY au minimum
docker compose up --build
```

Puis ouvrir http://localhost:8080. L'API de l'agent est aussi visible sur http://localhost:8000/docs.

## Déployer sur Aiven Runtime

1. Pousser le repo sur GitHub.
2. Console Aiven > Runtime > Deploy application, choisir le repo et `docker-compose.yml`, puis Scan et Deploy.
3. Sur l'app `agent` : Connected services > Connect service > le PostgreSQL existant, variable `DATABASE_URL`. Ajouter `ANTHROPIC_API_KEY` en secret.
4. Si le front affiche "Agent injoignable", mettre dans l'app `frontend` la variable `AGENT_URL` avec l'URL publique de l'app `agent`, puis redéployer.
5. Un push ne redéploie pas automatiquement : Overview > Actions > Deploy latest commit.

## Endpoints de l'agent

| Méthode | Route | Description |
|---|---|---|
| GET | `/health` | État de l'agent et de la base |
| GET | `/api/clients?q=` | Liste des clients |
| GET | `/api/clients/{id}` | Vue 360 : profil, cartes, KPIs, alertes, dernières opérations |
| GET | `/api/clients/{id}/monthly` | Dépenses mensuelles |
| POST | `/api/chat` | `{client_id, message, thread_id?}` : réponse de l'agent, outils utilisés, latence |
| POST | `/api/chat/stream` | `{client_id, message, thread_id?, brief?}` : flux SSE (`start`, `step`, `step_done`, `ui`, `token`, `answer`, `error`) ; l'agent pilote l'écran en direct |
| POST | `/api/clients/{id}/brief` | Fiche de préparation de rendez-vous |

## Données

Les transactions viennent du dataset Kaggle "Financial Transactions Dataset: Analytics" (synthétique). Les montants sont présentés en euros. Les dates sont décalées pour que la dernière transaction tombe la veille du chargement (décalage tracé dans la table `app_meta`). Les numéros de carte sont masqués et les CVV supprimés.
