"""Run sql/validation_queries.sql and print results; exit 1 if any gate fails.

Usage: python scripts/validate.py postgresql://user:pw@host/db
       python scripts/validate.py data/iris_staging.sqlite
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bess_mastr.config import DbConfig  # noqa: E402
from bess_mastr.store import Database  # noqa: E402

SQL = Path(__file__).resolve().parents[1] / "sql" / "validation_queries.sql"


def blocks(text: str):
    for chunk in re.split(r"^-- name: ", text, flags=re.M)[1:]:
        header, _, body = chunk.partition("\n")
        sql = "\n".join(line for line in body.splitlines() if not line.startswith("--")).strip().rstrip(";")
        yield header.strip(), sql


def main(target: str) -> int:
    url = target if "://" in target else f"sqlite:///{target}"
    db = Database(DbConfig(url=url))
    failed = 0
    for name, sql in blocks(SQL.read_text(encoding="utf-8")):
        rows = db.query(sql)
        print(f"\n== {name}")
        if not rows:
            print("(no rows)")
        else:
            cols = list(rows[0])
            print(" | ".join(cols))
            for r in rows:
                print(" | ".join("" if r[c] is None else str(r[c]) for c in cols))
        if name.startswith("gate_"):
            ok = rows[0]["violations"] == 0
            failed += not ok
            print("PASS" if ok else "FAIL")
    db.close()
    print(f"\n{'ALL GATES PASSED' if not failed else f'{failed} GATE(S) FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
