"""Connexion PostgreSQL (Aiven) partagée par les outils de l'agent et l'API."""
import json
import os
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

try:  # en local hors Docker, on lit le .env à la racine du repo
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL manquant")
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
        )
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


def query_readonly(sql: str, max_rows: int = 200, timeout_s: int = 5) -> list[dict]:
    """Exécute une requête dans une transaction en lecture seule avec timeout."""
    with pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = '{int(timeout_s)}s'")
                cur.execute(sql)
                return cur.fetchmany(max_rows) if cur.description else []


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def to_json(data) -> str:
    return json.dumps(data, default=_default, ensure_ascii=False)


def jsonable(data):
    return json.loads(to_json(data))
