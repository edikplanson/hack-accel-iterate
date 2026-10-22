"""Connexion PostgreSQL (Aiven) partagée par les outils de l'agent et l'API."""
import json
import os
import hashlib
from pathlib import Path
from threading import Lock
from datetime import date, datetime
from decimal import Decimal

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from psycopg.conninfo import conninfo_to_dict, make_conninfo

try:  # en local hors Docker, on lit le .env à la racine du repo
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pool: ConnectionPool | None = None
_lock = Lock()


def pool() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            url = os.getenv('DATABASE_URL', DATABASE_URL)
            if not url:
                raise RuntimeError("DATABASE_URL manquant")
            options = conninfo_to_dict(url)
            local = options.get('host','').lower() in {'localhost','127.0.0.1','::1','postgres'}
            if local and os.getenv('ALLOW_INSECURE_LOCAL','false').lower() == 'true':
                options.setdefault('sslmode','disable')
            else:
                options['sslmode']='verify-full'
                options['sslrootcert']=os.getenv('DATABASE_SSLROOTCERT') or options.get('sslrootcert') or 'system'
                pem=os.getenv('DATABASE_CA_CERT')
                if pem:
                    directory=Path.cwd()/'.tmp/codex/postgres-ca'
                    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
                    path=directory/(hashlib.sha256(pem.encode()).hexdigest()+'.pem')
                    path.write_text(pem)
                    path.chmod(0o600)
                    options['sslrootcert']=str(path)
            options['connect_timeout']='10'
            _pool = ConnectionPool(
                make_conninfo(**options), min_size=0, max_size=8, timeout=10,
                kwargs={"row_factory": dict_row, "autocommit": True,"options":"-c statement_timeout=15000"},
                open=True,
            )
    return _pool


def query(sql: str, params: dict | tuple | None = None) -> list[dict]:
    with pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute(sql, params)
        rows=cur.fetchmany(501) if cur.description else []
        if len(rows)>500:
            raise RuntimeError('Résultat trop volumineux; préciser la demande')
        return rows


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
