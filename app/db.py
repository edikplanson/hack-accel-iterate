"""Connexion PostgreSQL (Aiven) partagée par les scripts et l'API."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pool: ConnectionPool | None = None


def connect(**kwargs) -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL manquant (fichier .env)")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, **kwargs)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=8,
                               kwargs={"row_factory": dict_row}, open=True)
    return _pool


@contextmanager
def cursor():
    with pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur


def query(sql: str, params: dict | tuple | None = None) -> list[dict]:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []
