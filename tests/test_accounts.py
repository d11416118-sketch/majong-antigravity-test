import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.accounts import AccountError, AccountManager


class TestAccountManager(unittest.TestCase):
    def test_relative_database_path_is_anchored_to_project_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir).resolve()
            with patch("backend.accounts.PROJECT_ROOT", project_root):
                manager = AccountManager("data/custom.db")

            self.assertEqual(manager.db_path, project_root / "data" / "custom.db")
            self.assertTrue(manager.db_path.is_file())

    def test_register_login_and_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))

            registered = manager.register("alice", "secret123")
            self.assertEqual(registered["username"], "alice")
            self.assertTrue(registered["token"])
            self.assertEqual(manager.get_profile(registered["id"])["coins"], 10)
            self.assertEqual(manager.get_profile(registered["id"])["rank_level"], 1)
            self.assertEqual(manager.get_profile(registered["id"])["rank_name"], "一段")
            self.assertEqual(manager.get_profile(registered["id"])["rank_points"], 0)
            self.assertEqual(manager.get_profile(registered["id"])["theme_id"], "classic")

            logged_in = manager.login("alice", "secret123")
            self.assertEqual(logged_in["id"], registered["id"])
            self.assertNotEqual(logged_in["token"], registered["token"])

            resumed = manager.resume(logged_in["token"])
            self.assertEqual(resumed["id"], registered["id"])

    def test_duplicate_username_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            manager.register("alice", "secret123")

            with self.assertRaises(AccountError):
                manager.register("alice", "secret456")

    def test_update_character_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            account = manager.register("alice", "secret123")

            profile = manager.update_character(account["id"], "flair")
            self.assertEqual(profile["character_id"], "flair")
            self.assertEqual(manager.get_profile(account["id"])["character_id"], "flair")

            with self.assertRaises(AccountError):
                manager.update_character(account["id"], "unknown")

    def test_update_theme_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            account = manager.register("alice", "secret123")

            profile = manager.update_theme(account["id"], "cyber")
            self.assertEqual(profile["theme_id"], "cyber")
            self.assertEqual(manager.get_profile(account["id"])["theme_id"], "cyber")

            with self.assertRaises(AccountError):
                manager.update_theme(account["id"], "custom-css")

    def test_profiles_history_and_coin_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            alice = manager.register("alice", "secret123")
            bob = manager.register("bobby", "secret123")

            self.assertEqual(manager.get_profile(alice["id"])["coins"], 10)
            played_at = 1_700_000_000
            manager.record_match_results(
                [
                    {
                        "id": "hist_alice_1",
                        "account_id": alice["id"],
                        "username": "alice",
                        "match_id": "match_1",
                        "played_at": played_at,
                        "mode": "matchmaking",
                        "room_id": "123456",
                        "players": [{"uid": alice["id"], "name": "alice"}, {"uid": bob["id"], "name": "bobby"}],
                        "winner_uid": alice["id"],
                        "winner_name": "alice",
                        "is_draw": False,
                        "fan_total": 4,
                        "fan_breakdown": [{"name": "底", "value": 1}],
                        "round_detail": {"events": []},
                        "score_delta": 4,
                        "coin_delta": 40,
                        "final_rank": 1,
                        "duration_seconds": 33,
                        "dealer_info": {"lian_zhuang": 0},
                        "seat_idx": 0,
                        "result": "WIN",
                    },
                    {
                        "id": "hist_bob_1",
                        "account_id": bob["id"],
                        "username": "bobby",
                        "match_id": "match_1",
                        "played_at": played_at,
                        "mode": "matchmaking",
                        "room_id": "123456",
                        "players": [{"uid": alice["id"], "name": "alice"}, {"uid": bob["id"], "name": "bobby"}],
                        "winner_uid": alice["id"],
                        "winner_name": "alice",
                        "is_draw": False,
                        "fan_total": 4,
                        "fan_breakdown": [{"name": "底", "value": 1}],
                        "round_detail": {"events": []},
                        "score_delta": -4,
                        "coin_delta": -40,
                        "final_rank": 2,
                        "duration_seconds": 33,
                        "dealer_info": {"lian_zhuang": 0},
                        "seat_idx": 1,
                        "result": "LOSE",
                    },
                ]
            )

            self.assertEqual(manager.get_profile(alice["id"])["coins"], 50)
            self.assertEqual(manager.get_profile(alice["id"])["wins"], 1)
            history = manager.get_recent_history(alice["id"])
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["fan_total"], 4)
            self.assertEqual(history[0]["players"][0]["name"], "alice")

    def test_ranked_match_updates_rank_points(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            alice = manager.register("alice", "secret123")
            bob = manager.register("bobby", "secret123")

            manager.record_match_results(
                [
                    {
                        "id": "hist_alice_ranked",
                        "account_id": alice["id"],
                        "username": "alice",
                        "match_id": "ranked_1",
                        "played_at": 1_700_000_000,
                        "mode": "ranked",
                        "room_id": "123456",
                        "players": [{"uid": alice["id"], "name": "alice"}, {"uid": bob["id"], "name": "bobby"}],
                        "winner_uid": alice["id"],
                        "winner_name": "alice",
                        "is_draw": False,
                        "fan_total": 2,
                        "fan_breakdown": [],
                        "round_detail": {"events": []},
                        "score_delta": 2,
                        "coin_delta": 20,
                        "final_rank": 1,
                        "rank_delta": 30,
                        "duration_seconds": 30,
                        "dealer_info": {},
                        "seat_idx": 0,
                        "result": "WIN",
                    },
                    {
                        "id": "hist_bob_ranked",
                        "account_id": bob["id"],
                        "username": "bobby",
                        "match_id": "ranked_1",
                        "played_at": 1_700_000_000,
                        "mode": "ranked",
                        "room_id": "123456",
                        "players": [{"uid": alice["id"], "name": "alice"}, {"uid": bob["id"], "name": "bobby"}],
                        "winner_uid": alice["id"],
                        "winner_name": "alice",
                        "is_draw": False,
                        "fan_total": 2,
                        "fan_breakdown": [],
                        "round_detail": {"events": []},
                        "score_delta": -2,
                        "coin_delta": -20,
                        "final_rank": 4,
                        "rank_delta": -30,
                        "duration_seconds": 30,
                        "dealer_info": {},
                        "seat_idx": 1,
                        "result": "LOSE",
                    },
                ]
            )

            alice_profile = manager.get_profile(alice["id"])
            bob_profile = manager.get_profile(bob["id"])
            self.assertEqual(alice_profile["rank_points"], 30)
            self.assertEqual(alice_profile["ranked_games"], 1)
            self.assertEqual(alice_profile["ranked_wins"], 1)
            self.assertEqual(bob_profile["rank_points"], 0)
            self.assertEqual(bob_profile["ranked_games"], 1)
            history = manager.get_recent_history(alice["id"])
            self.assertEqual(history[0]["rank_delta"], 30)
            self.assertEqual(history[0]["rank_points_after"], 30)

    def test_history_trim_removes_related_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            alice = manager.register("alice", "secret123")

            for idx in range(55):
                manager.record_match_results(
                    [
                        {
                            "id": f"hist_alice_{idx}",
                            "account_id": alice["id"],
                            "username": "alice",
                            "match_id": f"match_{idx}",
                            "played_at": 1_700_000_000 + idx,
                            "mode": "matchmaking",
                            "room_id": "123456",
                            "players": [{"uid": alice["id"], "name": "alice"}],
                            "winner_uid": alice["id"],
                            "winner_name": "alice",
                            "is_draw": False,
                            "fan_total": 1,
                            "fan_breakdown": [],
                            "round_detail": {"events": []},
                            "score_delta": 1,
                            "coin_delta": 1,
                            "final_rank": 1,
                            "duration_seconds": 10,
                            "dealer_info": {"lian_zhuang": 0},
                            "seat_idx": 0,
                            "result": "WIN",
                        }
                    ]
                )

            self.assertEqual(len(manager.get_recent_history(alice["id"], 100)), 50)
            self.assertEqual(manager.get_profile(alice["id"])["games_played"], 50)
            with manager._connect() as conn:
                match_players = conn.execute("SELECT COUNT(*) AS count FROM match_players WHERE account_id = ?", (alice["id"],)).fetchone()
                coin_ledger = conn.execute("SELECT COUNT(*) AS count FROM coin_ledger WHERE account_id = ?", (alice["id"],)).fetchone()
            self.assertEqual(match_players["count"], 50)
            self.assertEqual(coin_ledger["count"], 50)

    def test_legacy_starting_coins_are_migrated_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "accounts.db")
            manager = AccountManager(db_path)
            created_at = 1_700_000_000
            with manager._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO accounts (id, username, salt, password_hash, session_token, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("acct_legacy", "legacy", "salt", "hash", None, created_at),
                )
                conn.execute(
                    """
                    INSERT INTO player_profiles (account_id, display_name, character_id, coins, games_played, wins, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("acct_legacy", "legacy", "default", 1000, 0, 0, created_at),
                )
                conn.execute(
                    """
                    INSERT INTO accounts (id, username, salt, password_hash, session_token, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("acct_touched", "touched", "salt", "hash", None, created_at),
                )
                conn.execute(
                    """
                    INSERT INTO player_profiles (account_id, display_name, character_id, coins, games_played, wins, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("acct_touched", "touched", "default", 1000, 0, 0, created_at),
                )
                conn.execute(
                    """
                    INSERT INTO coin_ledger (id, account_id, match_id, delta, balance_after, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("ledger_touched", "acct_touched", None, 5, 1000, "manual", created_at),
                )

            migrated = AccountManager(db_path)
            self.assertEqual(migrated.get_profile("acct_legacy")["coins"], 10)
            self.assertEqual(migrated.get_profile("acct_touched")["coins"], 1000)

    def test_daily_checkin_and_rename_task_rewards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            alice = manager.register("alice", "secret123")

            first_checkin = manager.claim_daily_checkin(alice["id"])
            second_checkin = manager.claim_daily_checkin(alice["id"])
            self.assertTrue(first_checkin["claimed"])
            self.assertFalse(second_checkin["claimed"])
            self.assertEqual(manager.get_profile(alice["id"])["coins"], 13)

            first_rename = manager.update_display_name(alice["id"], "Alice 新名")
            second_rename = manager.update_display_name(alice["id"], "Alice 二名")
            self.assertTrue(first_rename["task"]["claimed"])
            self.assertFalse(second_rename["task"]["claimed"])
            self.assertEqual(second_rename["profile"]["display_name"], "Alice 二名")
            self.assertEqual(manager.get_profile(alice["id"])["coins"], 15)

    def test_friend_request_accept_and_remove(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
            alice = manager.register("alice", "secret123")
            bob = manager.register("bobby", "secret123")

            sent = manager.send_friend_request(alice["id"], "bobby")
            self.assertEqual(sent["status"], "sent")
            bob_social = manager.get_social_summary(bob["id"], {alice["id"]})
            self.assertEqual(len(bob_social["incoming"]), 1)
            self.assertTrue(bob_social["incoming"][0]["online"])

            accepted = manager.respond_friend_request(bob["id"], alice["id"], True)
            self.assertEqual(accepted["status"], "accepted")
            alice_social = manager.get_social_summary(alice["id"], {bob["id"]})
            self.assertEqual(len(alice_social["friends"]), 1)
            self.assertTrue(alice_social["friends"][0]["online"])

            removed = manager.remove_friend(alice["id"], bob["id"])
            self.assertEqual(removed["status"], "removed")
            self.assertEqual(manager.get_social_summary(alice["id"])["friends"], [])


if __name__ == "__main__":
    unittest.main()
