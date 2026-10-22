#!/usr/bin/env bash
# Remplit la base Aiven PostgreSQL avec le dataset Kaggle "Financial Transactions Dataset: Analytics"
set -euo pipefail

DATA_DIR="${DATA_DIR:-/data}"
DATASET="computingvictor/transactions-fraud-datasets"
TX_MONTHS="${TX_MONTHS:-24}"

: "${DATABASE_URL:?DATABASE_URL manquant dans le .env}"

mkdir -p "$DATA_DIR"
cd "$DATA_DIR"

# 1. Téléchargement (sauté si les CSV sont déjà dans le dossier monté)
if [[ -f users_data.csv && -f cards_data.csv && -f transactions_data.csv ]]; then
  echo "CSV déjà présents dans $DATA_DIR, téléchargement sauté"
else
  : "${KAGGLE_USERNAME:?KAGGLE_USERNAME manquant dans le .env}"
  : "${KAGGLE_KEY:?KAGGLE_KEY manquant dans le .env}"
  echo "Téléchargement de $DATASET depuis Kaggle..."
  curl -fL --retry 3 -u "${KAGGLE_USERNAME}:${KAGGLE_KEY}" \
    -o dataset.zip "https://www.kaggle.com/api/v1/datasets/download/${DATASET}"
  unzip -o -q dataset.zip
  rm -f dataset.zip
  # Certains zips rangent les fichiers dans un sous-dossier : on les remonte
  find . -mindepth 2 -name '*.csv' -exec mv -f {} . \;
  find . -mindepth 2 -name '*.json' -exec mv -f {} . \;
fi

ls -lh ./*.csv

# 2. Test de connexion
echo "Connexion à la base..."
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "select version();" > /dev/null
echo "Connexion OK"

# 3. Chargement
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v tx_months="$TX_MONTHS" -f /app/load_kaggle.sql

echo "Seed terminé"
