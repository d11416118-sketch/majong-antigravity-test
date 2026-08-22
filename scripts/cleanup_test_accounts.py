"""Safely remove socket-test accounts created with a trailing eight-hex suffix."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.accounts import DEFAULT_DB_PATH


TEST_USERNAME_PATTERN = re.compile(r"^.+_[0-9a-f]{8}$")
ACCOUNT_TABLE_COLUMNS = {
    "player_profiles": "account_id",
    "match_history": "account_id",
    "match_players": "account_id",
    "coin_ledger": "account_id",
    "task_claims": "account_id",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or delete automated test accounts from the Mahjong SQLite database."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--apply", action="store_true", help="Create a backup and perform deletion")
    return parser.parse_args()


def matching_accounts(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT id, username FROM accounts ORDER BY created_at, id").fetchall()
    return [(row[0], row[1]) for row in rows if TEST_USERNAME_PATTERN.fullmatch(row[1] or "")]


def related_counts(conn: sqlite3.Connection, account_ids: list[str]) -> dict[str, int]:
    if not account_ids:
        return {"accounts": 0, **{table: 0 for table in ACCOUNT_TABLE_COLUMNS}, "friend_links": 0}

    conn.execute("DROP TABLE IF EXISTS temp.cleanup_account_ids")
    conn.execute("CREATE TEMP TABLE cleanup_account_ids (account_id TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO cleanup_account_ids (account_id) VALUES (?)",
        ((account_id,) for account_id in account_ids),
    )
    counts = {"accounts": len(account_ids)}
    for table, column in ACCOUNT_TABLE_COLUMNS.items():
        counts[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} IN (SELECT account_id FROM cleanup_account_ids)"
        ).fetchone()[0]
    counts["friend_links"] = conn.execute(
        """
        SELECT COUNT(*) FROM friend_links
        WHERE requester_id IN (SELECT account_id FROM cleanup_account_ids)
           OR addressee_id IN (SELECT account_id FROM cleanup_account_ids)
        """
    ).fetchone()[0]
    return counts


def backup_database(source: sqlite3.Connection, db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"mahjong-before-test-account-cleanup-{stamp}.db"
    with sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    return backup_path


def delete_accounts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM friend_links
        WHERE requester_id IN (SELECT account_id FROM cleanup_account_ids)
           OR addressee_id IN (SELECT account_id FROM cleanup_account_ids)
        """
    )
    for table, column in ACCOUNT_TABLE_COLUMNS.items():
        conn.execute(
            f"DELETE FROM {table} WHERE {column} IN (SELECT account_id FROM cleanup_account_ids)"
        )
    conn.execute("DELETE FROM accounts WHERE id IN (SELECT account_id FROM cleanup_account_ids)")


def main() -> int:
    args = parse_args()
    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        candidates = matching_accounts(conn)
        account_ids = [account_id for account_id, _username in candidates]
        counts = related_counts(conn, account_ids)
        conn.commit()
        result = {
            "database": str(db_path),
            "username_pattern": TEST_USERNAME_PATTERN.pattern,
            "mode": "apply" if args.apply else "dry-run",
            "matched_accounts": len(candidates),
            "related_rows": counts,
        }
        if not args.apply or not candidates:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        backup_path = backup_database(conn, db_path)
        conn.execute("BEGIN IMMEDIATE")
        delete_accounts(conn)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        remaining = matching_accounts(conn)
        if integrity != "ok" or foreign_key_errors or remaining:
            conn.rollback()
            raise RuntimeError(
                f"Cleanup validation failed: integrity={integrity!r}, "
                f"foreign_key_errors={len(foreign_key_errors)}, remaining={len(remaining)}"
            )
        conn.commit()
        result["backup"] = str(backup_path)
        result["remaining_test_accounts"] = 0
        result["integrity_check"] = integrity
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
