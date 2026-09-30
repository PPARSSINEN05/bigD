from __future__ import annotations

import argparse

from . import db, services


def main() -> int:
    parser = argparse.ArgumentParser(description="Exécute le planificateur idempotent des audits")
    parser.add_argument("--db", default=str(db.db_path_from_env()))
    args = parser.parse_args()
    db.migrate(args.db)
    with db.connection(args.db) as conn:
        result = services.run_scheduler(conn)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
