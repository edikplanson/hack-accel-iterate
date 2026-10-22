#!/usr/bin/env bash
# Remplit la base Aiven PostgreSQL avec le dataset Kaggle "Financial Transactions Dataset: Analytics"
# Compatible Aiven Runtime : idempotent, puis reste en vie avec une page de statut sur $PORT (8080).
set -uo pipefail

DATA_DIR="${DATA_DIR:-/data}"
DATASET="computingvictor/transactions-fraud-datasets"
TX_MONTHS="${TX_MONTHS:-24}"
PORT="${PORT:-8080}"
STATUS_DIR=/app/status
mkdir -p "$STATUS_DIR"

status() {
  echo "$1"
  printf '%s\n%s\n' "$(date -u +%FT%TZ)" "$1" > "$STATUS_DIR/index.html"
}

serve_forever() {
  status "$1"
  if [[ "${KEEP_ALIVE:-1}" == "1" ]]; then
    echo "Page de statut sur le port $PORT"
    exec python3 -m http.server "$PORT" --directory "$STATUS_DIR"
  fi
  exit "${2:-0}"
}

run_seed() {
  set -e
  : "${DATABASE_URL:?DATABASE_URL manquant}"

  status "Connexion à la base..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "select 1;" > /dev/null

  # Idempotence : on ne recharge pas si les données sont déjà là (sauf FORCE_RESEED=1)
  local has_table rows=0
  has_table=$(psql "$DATABASE_URL" -tAc "select count(*) from information_schema.tables where table_schema='public' and table_name='transactions';")
  if [[ "$has_table" == "1" ]]; then
    rows=$(psql "$DATABASE_URL" -tAc "select count(*) from transactions;")
  fi
  if [[ "$rows" -gt 0 && "${FORCE_RESEED:-0}" != "1" ]]; then
    status "Base déjà remplie ($rows transactions), seed sauté. Mettre FORCE_RESEED=1 pour recharger."
    return 0
  fi

  mkdir -p "$DATA_DIR"
  cd "$DATA_DIR"
  local downloaded=0
  if [[ -f users_data.csv && -f cards_data.csv && -f transactions_data.csv ]]; then
    echo "CSV déjà présents, téléchargement sauté"
  else
    : "${KAGGLE_USERNAME:?KAGGLE_USERNAME manquant}"
    : "${KAGGLE_KEY:?KAGGLE_KEY manquant}"
    status "Téléchargement de $DATASET depuis Kaggle..."
    curl -fL --retry 3 -u "${KAGGLE_USERNAME}:${KAGGLE_KEY}" \
      -o dataset.zip "https://www.kaggle.com/api/v1/datasets/download/${DATASET}"
    unzip -o -q dataset.zip
    rm -f dataset.zip
    find . -mindepth 2 -name '*.csv' -exec mv -f {} . \;
    find . -mindepth 2 -name '*.json' -exec mv -f {} . \;
    downloaded=1
  fi
  ls -lh ./*.csv

  status "Chargement en cours (quelques minutes)..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v tx_months="$TX_MONTHS" -f /app/load_kaggle.sql

  # On libère le disque du conteneur, seulement pour les fichiers qu'on a téléchargés nous-mêmes
  if [[ $downloaded -eq 1 ]]; then rm -f ./*.csv ./*.json; fi
  status "Seed terminé"
}

# Sous-shell hors d'un "if" pour que set -e s'applique bien dans run_seed
( run_seed )
rc=$?
if [[ $rc -eq 0 ]]; then
  serve_forever "$(tail -n 1 "$STATUS_DIR/index.html")" 0
else
  serve_forever "ERREUR pendant le seed (code $rc), voir les logs de l'application" 1
fi
