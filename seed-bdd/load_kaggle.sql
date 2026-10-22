-- Chargement du dataset Kaggle "Financial Transactions Dataset: Analytics" dans Aiven PostgreSQL
-- Lancé par seed.sh depuis le dossier qui contient les CSV :
--   psql "$DATABASE_URL" -v tx_months=24 -f load_kaggle.sql
-- Les colonnes monétaires du CSV sont au format "$1234.56" : on charge en texte puis on nettoie.

\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Tables de staging (tout en texte, chargement brut)
DROP TABLE IF EXISTS stg_users, stg_cards, stg_tx;

CREATE UNLOGGED TABLE stg_users (
  id text, current_age text, retirement_age text, birth_year text, birth_month text,
  gender text, address text, latitude text, longitude text, per_capita_income text,
  yearly_income text, total_debt text, credit_score text, num_credit_cards text
);
CREATE UNLOGGED TABLE stg_cards (
  id text, client_id text, card_brand text, card_type text, card_number text, expires text,
  cvv text, has_chip text, num_cards_issued text, credit_limit text, acct_open_date text,
  year_pin_last_changed text, card_on_dark_web text
);
CREATE UNLOGGED TABLE stg_tx (
  id text, date text, client_id text, card_id text, amount text, use_chip text,
  merchant_id text, merchant_city text, merchant_state text, zip text, mcc text, errors text
);

\echo 'Chargement users_data.csv'
\copy stg_users FROM 'users_data.csv' WITH (FORMAT csv, HEADER true)
\echo 'Chargement cards_data.csv'
\copy stg_cards FROM 'cards_data.csv' WITH (FORMAT csv, HEADER true)
\echo 'Chargement transactions_data.csv (quelques minutes)'
\copy stg_tx FROM 'transactions_data.csv' WITH (FORMAT csv, HEADER true)

-- 2. Tables typées
DROP TABLE IF EXISTS transactions, cards, clients CASCADE;

CREATE TABLE clients (
  client_id         int PRIMARY KEY,
  age               int,
  retirement_age    int,
  birth_year        int,
  birth_month       int,
  gender            text,
  address           text,
  latitude          numeric,
  longitude         numeric,
  per_capita_income numeric,
  yearly_income     numeric,
  total_debt        numeric,
  credit_score      int,
  num_credit_cards  int
);

INSERT INTO clients
SELECT id::int, current_age::int, retirement_age::int, birth_year::int, birth_month::int,
       gender, address, latitude::numeric, longitude::numeric,
       replace(per_capita_income, '$', '')::numeric,
       replace(yearly_income, '$', '')::numeric,
       replace(total_debt, '$', '')::numeric,
       credit_score::int, num_credit_cards::int
FROM stg_users;

CREATE TABLE cards (
  card_id               int PRIMARY KEY,
  client_id             int REFERENCES clients(client_id),
  card_brand            text,
  card_type             text,
  card_number_masked    text,      -- on ne garde que les 4 derniers chiffres, pas de CVV
  expires               date,
  has_chip              boolean,
  num_cards_issued      int,
  credit_limit          numeric,
  acct_open_date        date,
  year_pin_last_changed int,
  card_on_dark_web      boolean
);

INSERT INTO cards
SELECT id::int, client_id::int, card_brand, card_type,
       '**** ' || right(card_number, 4),
       (to_date(expires, 'MM/YYYY') + interval '1 month' - interval '1 day')::date,
       upper(has_chip) = 'YES',
       num_cards_issued::int,
       replace(credit_limit, '$', '')::numeric,
       to_date(acct_open_date, 'MM/YYYY'),
       year_pin_last_changed::int,
       upper(card_on_dark_web) = 'YES'
FROM stg_cards;

-- Transactions : on garde les N derniers mois du dataset (variable tx_months, 24 par défaut)
CREATE TABLE transactions (
  tx_id          bigint PRIMARY KEY,
  tx_date        timestamp NOT NULL,
  client_id      int REFERENCES clients(client_id),
  card_id        int REFERENCES cards(card_id),
  amount         numeric(12,2),
  use_chip       text,
  merchant_id    int,
  merchant_city  text,
  merchant_state text,
  zip            text,
  mcc            int,
  errors         text
);

INSERT INTO transactions
SELECT id::bigint, date::timestamp, client_id::int, card_id::int,
       replace(amount, '$', '')::numeric,
       use_chip, merchant_id::int, merchant_city, merchant_state,
       nullif(regexp_replace(zip, '\.0$', ''), ''), mcc::int, nullif(errors, '')
FROM stg_tx
WHERE date::timestamp >= (SELECT max(date::timestamp) FROM stg_tx) - (:'tx_months' || ' months')::interval;

CREATE INDEX idx_tx_client_date ON transactions (client_id, tx_date DESC);
CREATE INDEX idx_tx_card ON transactions (card_id);
CREATE INDEX idx_cards_client ON cards (client_id);

-- 3. Table pour les PDF de règles (RAG via pgvector)
-- Dimension 1024 par défaut : à ajuster selon le modèle d'embedding choisi
CREATE TABLE IF NOT EXISTS policy_chunks (
  chunk_id   bigserial PRIMARY KEY,
  doc_name   text NOT NULL,
  section    text,
  page       int,
  content    text NOT NULL,
  embedding  vector(1024)
);

-- 4. Nettoyage
DROP TABLE stg_users, stg_cards, stg_tx;
ANALYZE;

\echo 'Terminé. Vérification :'
SELECT (SELECT count(*) FROM clients) AS clients,
       (SELECT count(*) FROM cards) AS cartes,
       (SELECT count(*) FROM transactions) AS transactions,
       (SELECT min(tx_date)::date FROM transactions) AS debut,
       (SELECT max(tx_date)::date FROM transactions) AS fin;
