"""Apply db/migrations/*.sql that aren't in schema_migrations yet (P4).

Each file wraps itself in BEGIN/COMMIT and inserts its own version row.

    .venv/Scripts/python scripts/db_migrate.py          # apply pending
    .venv/Scripts/python scripts/db_migrate.py --list   # show status only
"""
from __future__ import annotations

import sys

from pg_common import REPO, connect

MIGRATIONS = REPO / "db" / "migrations"


def applied(conn) -> set[str]:
    exists = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0]
    if not exists:
        return set()
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def main() -> None:
    with connect(autocommit=True) as conn:
        done = applied(conn)
        files = sorted(MIGRATIONS.glob("*.sql"))
        for f in files:
            mark = "applied" if f.stem in done else "pending"
            if "--list" in sys.argv or f.stem in done:
                print(f"{mark:8} {f.name}")
                continue
            conn.execute(f.read_text(encoding="utf-8"))
            if f.stem not in applied(conn):
                raise SystemExit(f"{f.name} ran but did not record version {f.stem}")
            print(f"applied  {f.name}")


if __name__ == "__main__":
    main()
