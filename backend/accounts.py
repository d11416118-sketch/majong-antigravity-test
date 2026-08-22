import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from contextlib import contextmanager


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "mahjong.db"


class AccountError(ValueError):
    pass


STARTING_COINS = 10
LEGACY_STARTING_COINS = 1000
PLAY_ROUND_REWARD = 1
DAILY_CHECKIN_REWARD = 3
ONLINE_REWARD_COINS = 5
ONLINE_REWARD_SECONDS = 30 * 60
RENAME_TASK_REWARD = 2
TAIPEI_TZ = timezone(timedelta(hours=8))
ALLOWED_CHARACTER_IDS = {"default", "flair"}
ALLOWED_THEME_IDS = {"classic", "teahouse", "cyber", "imperial"}
RANK_TIERS = (
    {"level": 1, "name": "一段", "min_points": 0},
    {"level": 2, "name": "二段", "min_points": 100},
    {"level": 3, "name": "三段", "min_points": 250},
    {"level": 4, "name": "四段", "min_points": 450},
    {"level": 5, "name": "五段", "min_points": 700},
)
RANKED_POINT_DELTAS = {1: 30, 2: 10, 3: -10, 4: -30}


def rank_payload(points: int) -> Dict:
    points = max(0, int(points or 0))
    current = RANK_TIERS[0]
    for tier in RANK_TIERS:
        if points >= tier["min_points"]:
            current = tier
        else:
            break

    next_tier = next((tier for tier in RANK_TIERS if tier["level"] == current["level"] + 1), None)
    return {
        "rank_points": points,
        "rank_level": current["level"],
        "rank_name": current["name"],
        "rank_floor": current["min_points"],
        "next_rank_points": next_tier["min_points"] if next_tier else None,
    }


class AccountManager:
    def __init__(self, db_path: Optional[str] = None):
        configured_path = db_path if db_path is not None else os.environ.get("MAHJONG_DB_PATH")
        path = Path(configured_path).expanduser() if configured_path else DEFAULT_DB_PATH
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        self.db_path = path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def register(self, username: str, password: str) -> Dict:
        username = self._normalize_username(username)
        self._validate_password(password)
        account_id = f"acct_{secrets.token_hex(8)}"
        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)
        token = secrets.token_urlsafe(32)

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO accounts (id, username, salt, password_hash, session_token, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (account_id, username, salt, password_hash, token, int(time.time())),
                )
                conn.execute(
                    """
                    INSERT INTO player_profiles (
                        account_id, display_name, character_id, theme_id, coins, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (account_id, username, "default", "classic", STARTING_COINS, int(time.time())),
                )
        except sqlite3.IntegrityError as exc:
            raise AccountError("Username already exists") from exc

        return {"id": account_id, "username": username, "token": token, "active_room_id": None}

    def login(self, username: str, password: str) -> Dict:
        username = self._normalize_username(username)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()

        if not row:
            raise AccountError("帳號或密碼錯誤")

        expected = self._hash_password(password, row["salt"])
        if not hmac.compare_digest(expected, row["password_hash"]):
            raise AccountError("帳號或密碼錯誤")

        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute("UPDATE accounts SET session_token = ? WHERE id = ?", (token, row["id"]))

        return {
            "id": row["id"],
            "username": row["username"],
            "token": token,
            "active_room_id": row["active_room_id"],
        }

    def resume(self, token: str) -> Optional[Dict]:
        if not token:
            return None

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE session_token = ?", (token,)).fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "username": row["username"],
            "token": token,
            "active_room_id": row["active_room_id"],
        }

    def set_active_room(self, account_id: str, room_id: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE accounts SET active_room_id = ? WHERE id = ?", (room_id, account_id))

    def add_coins(self, account_id: str, amount: int, reason: str, match_id: Optional[str] = None) -> Optional[Dict]:
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            self._apply_coin_delta(conn, account_id, int(amount), reason, match_id)
            row = self._get_profile_row(conn, account_id)
        return self._profile_payload(row) if row else None

    def get_profile(self, account_id: str) -> Optional[Dict]:
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            row = self._get_profile_row(conn, account_id)
        return self._profile_payload(row) if row else None

    def get_public_profile_by_username(self, username: str) -> Optional[Dict]:
        username = self._normalize_username(username)
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM accounts WHERE username = ?", (username,)).fetchone()
            if not row:
                return None
            self._ensure_profile(conn, row["id"])
            profile = self._get_profile_row(conn, row["id"])
        return self._profile_payload(profile) if profile else None

    def get_reward_summary(self, account_id: str) -> Dict:
        today = self._today_key()
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            profile = self._get_profile_row(conn, account_id)
            claimed_tasks = {
                row["task_key"]
                for row in conn.execute(
                    "SELECT task_key FROM task_claims WHERE account_id = ?",
                    (account_id,),
                ).fetchall()
            }
        return {
            "daily": {
                "reward": DAILY_CHECKIN_REWARD,
                "claimed": bool(profile and profile["last_checkin_day"] == today),
            },
            "online": {
                "reward": ONLINE_REWARD_COINS,
                "interval_seconds": ONLINE_REWARD_SECONDS,
            },
            "tasks": [
                {
                    "key": "rename_display_name",
                    "label": "首次改名",
                    "reward": RENAME_TASK_REWARD,
                    "claimed": "rename_display_name" in claimed_tasks,
                }
            ],
        }

    def claim_daily_checkin(self, account_id: str) -> Dict:
        today = self._today_key()
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            profile = self._get_profile_row(conn, account_id)
            if profile and profile["last_checkin_day"] == today:
                return {"claimed": False, "reward": 0, "profile": self._profile_payload(profile)}

            conn.execute(
                "UPDATE player_profiles SET last_checkin_day = ? WHERE account_id = ?",
                (today, account_id),
            )
            self._apply_coin_delta(conn, account_id, DAILY_CHECKIN_REWARD, "daily_checkin")
            profile = self._get_profile_row(conn, account_id)
        return {"claimed": True, "reward": DAILY_CHECKIN_REWARD, "profile": self._profile_payload(profile)}

    def update_display_name(self, account_id: str, display_name: str) -> Dict:
        display_name = self._normalize_display_name(display_name)
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            conn.execute(
                "UPDATE player_profiles SET display_name = ? WHERE account_id = ?",
                (display_name, account_id),
            )
            task = self._claim_task(conn, account_id, "rename_display_name", RENAME_TASK_REWARD)
            profile = self._get_profile_row(conn, account_id)
        return {"profile": self._profile_payload(profile), "task": task}

    def update_character(self, account_id: str, character_id: str) -> Dict:
        character_id = self._normalize_character_id(character_id)
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            conn.execute(
                "UPDATE player_profiles SET character_id = ? WHERE account_id = ?",
                (character_id, account_id),
            )
            profile = self._get_profile_row(conn, account_id)
        return self._profile_payload(profile)

    def update_theme(self, account_id: str, theme_id: str) -> Dict:
        theme_id = self._normalize_theme_id(theme_id)
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            conn.execute(
                "UPDATE player_profiles SET theme_id = ? WHERE account_id = ?",
                (theme_id, account_id),
            )
            profile = self._get_profile_row(conn, account_id)
        return self._profile_payload(profile)

    def get_social_summary(self, account_id: str, online_ids: Optional[set[str]] = None) -> Dict:
        online_ids = online_ids or set()
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            rows = conn.execute(
                """
                SELECT *
                FROM friend_links
                WHERE requester_id = ? OR addressee_id = ?
                ORDER BY updated_at DESC
                """,
                (account_id, account_id),
            ).fetchall()

            friends = []
            incoming = []
            outgoing = []
            for row in rows:
                requester_id = row["requester_id"]
                addressee_id = row["addressee_id"]
                other_id = addressee_id if requester_id == account_id else requester_id
                profile = self._get_profile_row(conn, other_id)
                if not profile:
                    continue
                item = self._social_profile(profile, other_id in online_ids)
                if row["status"] == "ACCEPTED":
                    friends.append(item)
                elif row["status"] == "PENDING" and addressee_id == account_id:
                    incoming.append(item)
                elif row["status"] == "PENDING" and requester_id == account_id:
                    outgoing.append(item)

        return {"friends": friends, "incoming": incoming, "outgoing": outgoing}

    def send_friend_request(self, account_id: str, username: str) -> Dict:
        username = self._normalize_username(username)
        with self._connect() as conn:
            self._ensure_profile(conn, account_id)
            target = conn.execute("SELECT id FROM accounts WHERE username = ?", (username,)).fetchone()
            if not target:
                raise AccountError("Player not found")
            target_id = target["id"]
            if target_id == account_id:
                raise AccountError("Cannot add yourself")
            self._ensure_profile(conn, target_id)

            existing = self._get_friend_link(conn, account_id, target_id)
            now = int(time.time())
            if existing:
                if existing["status"] == "ACCEPTED":
                    return {"status": "already_friends", "target_id": target_id}
                if existing["requester_id"] == account_id:
                    return {"status": "pending", "target_id": target_id}
                conn.execute(
                    """
                    UPDATE friend_links
                    SET status = 'ACCEPTED', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, existing["id"]),
                )
                return {"status": "accepted", "target_id": target_id}

            conn.execute(
                """
                INSERT INTO friend_links (id, requester_id, addressee_id, status, created_at, updated_at)
                VALUES (?, ?, ?, 'PENDING', ?, ?)
                """,
                (f"friend_{secrets.token_hex(10)}", account_id, target_id, now, now),
            )
        return {"status": "sent", "target_id": target_id}

    def respond_friend_request(self, account_id: str, requester_id: str, accept: bool) -> Dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM friend_links
                WHERE requester_id = ? AND addressee_id = ? AND status = 'PENDING'
                """,
                (requester_id, account_id),
            ).fetchone()
            if not row:
                raise AccountError("Friend request not found")

            if accept:
                conn.execute(
                    "UPDATE friend_links SET status = 'ACCEPTED', updated_at = ? WHERE id = ?",
                    (int(time.time()), row["id"]),
                )
                status = "accepted"
            else:
                conn.execute("DELETE FROM friend_links WHERE id = ?", (row["id"],))
                status = "declined"
        return {"status": status, "target_id": requester_id}

    def remove_friend(self, account_id: str, friend_id: str) -> Dict:
        with self._connect() as conn:
            row = self._get_friend_link(conn, account_id, friend_id)
            if not row:
                raise AccountError("Friend not found")
            conn.execute("DELETE FROM friend_links WHERE id = ?", (row["id"],))
        return {"status": "removed", "target_id": friend_id}

    def get_recent_history(self, account_id: str, limit: int = 50) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM match_history
                WHERE account_id = ?
                ORDER BY played_at DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        return [self._decode_history_row(row) for row in rows]

    def record_match_results(self, records: List[Dict]) -> None:
        if not records:
            return

        with self._connect() as conn:
            for record in records:
                account_id = record["account_id"]
                self._ensure_profile(conn, account_id)
                rank_delta = int(record.get("rank_delta", 0) or 0) if record.get("mode") == "ranked" else 0
                rank_points_after = self._apply_rank_delta(conn, account_id, rank_delta)
                if record.get("mode") == "ranked":
                    conn.execute(
                        """
                        UPDATE player_profiles
                        SET ranked_games = ranked_games + 1,
                            ranked_wins = ranked_wins + ?
                        WHERE account_id = ?
                        """,
                        (1 if record.get("result") == "WIN" else 0, account_id),
                    )
                conn.execute(
                    """
                    INSERT INTO match_history (
                        id, account_id, match_id, played_at, mode, room_id, players_json,
                        winner_uid, winner_name, is_draw, fan_total, fan_breakdown_json,
                        round_detail_json, score_delta, coin_delta, final_rank,
                        rank_delta, rank_points_after, duration_seconds, dealer_info_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        account_id,
                        record["match_id"],
                        record["played_at"],
                        record["mode"],
                        record["room_id"],
                        json.dumps(record["players"], ensure_ascii=False),
                        record.get("winner_uid"),
                        record.get("winner_name"),
                        1 if record.get("is_draw") else 0,
                        int(record.get("fan_total", 0)),
                        json.dumps(record.get("fan_breakdown", []), ensure_ascii=False),
                        json.dumps(record.get("round_detail", {}), ensure_ascii=False),
                        int(record.get("score_delta", 0)),
                        int(record.get("coin_delta", 0)),
                        int(record.get("final_rank", 0)),
                        rank_delta,
                        rank_points_after,
                        int(record.get("duration_seconds", 0)),
                        json.dumps(record.get("dealer_info", {}), ensure_ascii=False),
                    ),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO match_players (
                        match_id, account_id, username, seat_idx, score_delta,
                        coin_delta, final_rank, result
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["match_id"],
                        account_id,
                        record.get("username", ""),
                        int(record.get("seat_idx", 0)),
                        int(record.get("score_delta", 0)),
                        int(record.get("coin_delta", 0)),
                        int(record.get("final_rank", 0)),
                        record.get("result", ""),
                    ),
                )
                self._trim_history(conn, account_id, 50)

                coin_delta = int(record.get("coin_delta", 0))
                if coin_delta:
                    conn.execute(
                        "UPDATE player_profiles SET coins = coins + ? WHERE account_id = ?",
                        (coin_delta, account_id),
                    )
                    balance = conn.execute(
                        "SELECT coins FROM player_profiles WHERE account_id = ?",
                        (account_id,),
                    ).fetchone()["coins"]
                    conn.execute(
                        """
                        INSERT INTO coin_ledger (
                            id, account_id, match_id, delta, balance_after, reason, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"ledger_{secrets.token_hex(10)}",
                            account_id,
                            record["match_id"],
                            coin_delta,
                            balance,
                            record.get("coin_reason", "match"),
                            record["played_at"],
                        ),
                    )

            for record in records:
                self._update_profile_stats(conn, record["account_id"])

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    session_token TEXT,
                    active_room_id TEXT,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_profiles (
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    character_id TEXT,
                    theme_id TEXT NOT NULL DEFAULT 'classic',
                    coins INTEGER NOT NULL DEFAULT 10,
                    games_played INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    rank_points INTEGER NOT NULL DEFAULT 0,
                    ranked_games INTEGER NOT NULL DEFAULT 0,
                    ranked_wins INTEGER NOT NULL DEFAULT 0,
                    last_checkin_day TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
                """
            )
            self._ensure_profile_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS match_history (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    match_id TEXT NOT NULL,
                    played_at INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    room_id TEXT NOT NULL,
                    players_json TEXT NOT NULL,
                    winner_uid TEXT,
                    winner_name TEXT,
                    is_draw INTEGER NOT NULL,
                    fan_total INTEGER NOT NULL,
                    fan_breakdown_json TEXT NOT NULL,
                    round_detail_json TEXT NOT NULL,
                    score_delta INTEGER NOT NULL,
                    coin_delta INTEGER NOT NULL,
                    final_rank INTEGER NOT NULL,
                    rank_delta INTEGER NOT NULL DEFAULT 0,
                    rank_points_after INTEGER NOT NULL DEFAULT 0,
                    duration_seconds INTEGER NOT NULL,
                    dealer_info_json TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
                """
            )
            self._ensure_match_history_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_match_history_account_time ON match_history(account_id, played_at DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS match_players (
                    match_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    seat_idx INTEGER NOT NULL,
                    score_delta INTEGER NOT NULL,
                    coin_delta INTEGER NOT NULL,
                    final_rank INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY (match_id, account_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS coin_ledger (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    match_id TEXT,
                    delta INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_claims (
                    account_id TEXT NOT NULL,
                    task_key TEXT NOT NULL,
                    reward INTEGER NOT NULL,
                    claimed_at INTEGER NOT NULL,
                    PRIMARY KEY (account_id, task_key),
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS friend_links (
                    id TEXT PRIMARY KEY,
                    requester_id TEXT NOT NULL,
                    addressee_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE (requester_id, addressee_id),
                    FOREIGN KEY (requester_id) REFERENCES accounts(id),
                    FOREIGN KEY (addressee_id) REFERENCES accounts(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_friend_links_requester ON friend_links(requester_id, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_friend_links_addressee ON friend_links(addressee_id, status)"
            )
            self._ensure_registered_profiles(conn)
            self._migrate_legacy_starting_coins(conn)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_profile_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(player_profiles)").fetchall()}
        if "last_checkin_day" not in columns:
            conn.execute("ALTER TABLE player_profiles ADD COLUMN last_checkin_day TEXT NOT NULL DEFAULT ''")
        if "rank_points" not in columns:
            conn.execute("ALTER TABLE player_profiles ADD COLUMN rank_points INTEGER NOT NULL DEFAULT 0")
        if "ranked_games" not in columns:
            conn.execute("ALTER TABLE player_profiles ADD COLUMN ranked_games INTEGER NOT NULL DEFAULT 0")
        if "ranked_wins" not in columns:
            conn.execute("ALTER TABLE player_profiles ADD COLUMN ranked_wins INTEGER NOT NULL DEFAULT 0")
        if "theme_id" not in columns:
            conn.execute("ALTER TABLE player_profiles ADD COLUMN theme_id TEXT NOT NULL DEFAULT 'classic'")

    def _ensure_match_history_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(match_history)").fetchall()}
        if "rank_delta" not in columns:
            conn.execute("ALTER TABLE match_history ADD COLUMN rank_delta INTEGER NOT NULL DEFAULT 0")
        if "rank_points_after" not in columns:
            conn.execute("ALTER TABLE match_history ADD COLUMN rank_points_after INTEGER NOT NULL DEFAULT 0")

    def _get_profile_row(self, conn: sqlite3.Connection, account_id: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            """
            SELECT p.*, a.username
            FROM player_profiles p
            JOIN accounts a ON a.id = p.account_id
            WHERE p.account_id = ?
            """,
            (account_id,),
        ).fetchone()

    def _profile_payload(self, row: sqlite3.Row) -> Dict:
        data = dict(row)
        data.update(rank_payload(int(data.get("rank_points", 0) or 0)))
        return data

    def _apply_rank_delta(self, conn: sqlite3.Connection, account_id: str, delta: int) -> int:
        row = conn.execute(
            "SELECT rank_points FROM player_profiles WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        current = int(row["rank_points"] if row else 0)
        updated = max(0, current + int(delta or 0))
        if updated != current:
            conn.execute(
                "UPDATE player_profiles SET rank_points = ? WHERE account_id = ?",
                (updated, account_id),
            )
        return updated

    def _apply_coin_delta(
        self,
        conn: sqlite3.Connection,
        account_id: str,
        delta: int,
        reason: str,
        match_id: Optional[str] = None,
        created_at: Optional[int] = None,
    ) -> int:
        if delta == 0:
            row = conn.execute("SELECT coins FROM player_profiles WHERE account_id = ?", (account_id,)).fetchone()
            return int(row["coins"]) if row else 0

        created_at = int(created_at or time.time())
        conn.execute(
            "UPDATE player_profiles SET coins = coins + ? WHERE account_id = ?",
            (delta, account_id),
        )
        balance = conn.execute(
            "SELECT coins FROM player_profiles WHERE account_id = ?",
            (account_id,),
        ).fetchone()["coins"]
        conn.execute(
            """
            INSERT INTO coin_ledger (
                id, account_id, match_id, delta, balance_after, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"ledger_{secrets.token_hex(10)}",
                account_id,
                match_id,
                delta,
                balance,
                reason,
                created_at,
            ),
        )
        return int(balance)

    def _claim_task(self, conn: sqlite3.Connection, account_id: str, task_key: str, reward: int) -> Dict:
        existing = conn.execute(
            "SELECT task_key FROM task_claims WHERE account_id = ? AND task_key = ?",
            (account_id, task_key),
        ).fetchone()
        if existing:
            return {"key": task_key, "claimed": False, "reward": 0}

        now = int(time.time())
        conn.execute(
            """
            INSERT INTO task_claims (account_id, task_key, reward, claimed_at)
            VALUES (?, ?, ?, ?)
            """,
            (account_id, task_key, reward, now),
        )
        self._apply_coin_delta(conn, account_id, reward, f"task:{task_key}", created_at=now)
        return {"key": task_key, "claimed": True, "reward": reward}

    def _get_friend_link(self, conn: sqlite3.Connection, left_id: str, right_id: str) -> Optional[sqlite3.Row]:
        return conn.execute(
            """
            SELECT *
            FROM friend_links
            WHERE (requester_id = ? AND addressee_id = ?)
               OR (requester_id = ? AND addressee_id = ?)
            """,
            (left_id, right_id, right_id, left_id),
        ).fetchone()

    def _social_profile(self, profile: sqlite3.Row, online: bool) -> Dict:
        rank = rank_payload(int(profile["rank_points"] or 0))
        return {
            "account_id": profile["account_id"],
            "username": profile["username"],
            "display_name": profile["display_name"] or profile["username"],
            "theme_id": profile["theme_id"] or "classic",
            "games_played": profile["games_played"],
            "wins": profile["wins"],
            "rank_points": rank["rank_points"],
            "rank_level": rank["rank_level"],
            "rank_name": rank["rank_name"],
            "ranked_games": profile["ranked_games"],
            "ranked_wins": profile["ranked_wins"],
            "online": online,
        }

    def _ensure_profile(self, conn: sqlite3.Connection, account_id: str) -> None:
        row = conn.execute("SELECT username FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            return
        conn.execute(
            """
            INSERT OR IGNORE INTO player_profiles (
                account_id, display_name, character_id, theme_id, coins, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (account_id, row["username"], "default", "classic", STARTING_COINS, int(time.time())),
        )

    def _ensure_registered_profiles(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO player_profiles (
                account_id, display_name, character_id, theme_id, coins, created_at
            )
            SELECT id, username, 'default', 'classic', ?, created_at
            FROM accounts
            """,
            (STARTING_COINS,),
        )

    def _migrate_legacy_starting_coins(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE player_profiles
            SET coins = ?
            WHERE coins = ?
              AND games_played = 0
              AND wins = 0
              AND NOT EXISTS (
                  SELECT 1 FROM match_history
                  WHERE match_history.account_id = player_profiles.account_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM match_players
                  WHERE match_players.account_id = player_profiles.account_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM coin_ledger
                  WHERE coin_ledger.account_id = player_profiles.account_id
              )
            """,
            (STARTING_COINS, LEGACY_STARTING_COINS),
        )

    @staticmethod
    def _today_key() -> str:
        return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")

    def _trim_history(self, conn: sqlite3.Connection, account_id: str, keep: int) -> None:
        conn.execute(
            """
            DELETE FROM match_history
            WHERE account_id = ?
              AND id NOT IN (
                  SELECT id FROM match_history
                  WHERE account_id = ?
                  ORDER BY played_at DESC
                  LIMIT ?
              )
            """,
            (account_id, account_id, keep),
        )
        conn.execute(
            """
            DELETE FROM match_players
            WHERE account_id = ?
              AND match_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM match_history
                  WHERE match_history.account_id = match_players.account_id
                    AND match_history.match_id = match_players.match_id
              )
            """,
            (account_id,),
        )
        conn.execute(
            """
            DELETE FROM coin_ledger
            WHERE account_id = ?
              AND match_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM match_history
                  WHERE match_history.account_id = coin_ledger.account_id
                    AND match_history.match_id = coin_ledger.match_id
              )
            """,
            (account_id,),
        )

    def _update_profile_stats(self, conn: sqlite3.Connection, account_id: str) -> None:
        stats = conn.execute(
            """
            SELECT COUNT(*) AS games_played,
                   SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) AS wins
            FROM match_players
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        conn.execute(
            "UPDATE player_profiles SET games_played = ?, wins = ? WHERE account_id = ?",
            (stats["games_played"] or 0, stats["wins"] or 0, account_id),
        )

    @staticmethod
    def _decode_history_row(row: sqlite3.Row) -> Dict:
        data = dict(row)
        data["is_draw"] = bool(data["is_draw"])
        data["players"] = json.loads(data.pop("players_json") or "[]")
        data["fan_breakdown"] = json.loads(data.pop("fan_breakdown_json") or "[]")
        data["round_detail"] = json.loads(data.pop("round_detail_json") or "{}")
        data["dealer_info"] = json.loads(data.pop("dealer_info_json") or "{}")
        return data

    @staticmethod
    def _normalize_username(username: str) -> str:
        value = (username or "").strip()
        if len(value) < 3 or len(value) > 20:
            raise AccountError("Username must be 3-20 characters")
        return value

    @staticmethod
    def _normalize_display_name(display_name: str) -> str:
        value = re.sub(r"\s+", " ", (display_name or "").strip())
        if len(value) < 2 or len(value) > 20:
            raise AccountError("Display name must be 2-20 characters")
        return value

    @staticmethod
    def _normalize_character_id(character_id: str) -> str:
        value = (character_id or "").strip().lower()
        if value not in ALLOWED_CHARACTER_IDS:
            raise AccountError("Invalid character")
        return value

    @staticmethod
    def _normalize_theme_id(theme_id: str) -> str:
        value = (theme_id or "").strip().lower()
        if value not in ALLOWED_THEME_IDS:
            raise AccountError("Invalid theme")
        return value

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password or "") < 6:
            raise AccountError("Password must be at least 6 characters")

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return digest.hex()
