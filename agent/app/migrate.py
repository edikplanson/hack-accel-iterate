"""Create application state only; never alter the existing banking tables."""
from pathlib import Path

from .db import pool


def migrate():
    with pool().connection() as conn, conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(72621022)')
        conn.execute('CREATE SCHEMA IF NOT EXISTS app')
        conn.execute('CREATE TABLE IF NOT EXISTS app.schema_migrations(name text PRIMARY KEY)')
        for path in sorted((Path(__file__).parent/'migrations').glob('*.sql')):
            if not conn.execute('SELECT name FROM app.schema_migrations WHERE name=%s',(path.name,)).fetchone():
                conn.execute(path.read_text())
                conn.execute('INSERT INTO app.schema_migrations(name) VALUES(%s)',(path.name,))
                print(f'Applied {path.name}')


if __name__=='__main__':
    migrate()
