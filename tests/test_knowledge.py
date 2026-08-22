import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.game_engine import GameEngine, STATE_PLAYER_TURN
from backend.knowledge import build_visible_state


class TestPlayerKnowledge(unittest.TestCase):
    def make_game(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.state = STATE_PLAYER_TURN
        game.current_turn_idx = 0
        game.players[0].hand = ["1m", "2m"]
        game.players[1].hand = ["9m", "9m"]
        game.players[2].discards = ["5p"]
        game.players[3].melds = [{"type": "PON", "tile": "7s", "tiles": ["7s", "7s", "7s"], "from_idx": 2}]
        game.event_log = [
            {"seq": 1, "type": "DRAW", "uid": "u0", "player_idx": 0, "tile": "2m"},
            {"seq": 2, "type": "DRAW", "uid": "u1", "player_idx": 1, "tile": "9m"},
            {"seq": 3, "type": "DISCARD", "uid": "u2", "player_idx": 2, "tile": "5p"},
        ]
        return game

    def test_history_hides_other_players_drawn_tiles(self):
        view = build_visible_state(self.make_game(), "u0")
        self.assertEqual(view["history"][0]["tile"], "2m")
        self.assertIsNone(view["history"][1]["tile"])
        self.assertTrue(view["history"][1]["hidden"])
        self.assertEqual(view["history"][2]["tile"], "5p")

    def test_history_hides_other_players_ting_waits(self):
        game = self.make_game()
        game._record_event(
            "TING",
            uid="u1",
            player_idx=1,
            tile="1m",
            ting_tiles=["2m", "5m"],
        )

        opponent_view = build_visible_state(game, "u0")
        owner_view = build_visible_state(game, "u1")

        self.assertNotIn("ting_tiles", opponent_view["history"][-1])
        self.assertEqual(owner_view["history"][-1]["ting_tiles"], ["2m", "5m"])

    def test_tile_tracker_counts_self_hand_and_public_tiles(self):
        view = build_visible_state(self.make_game(), "u0")
        by_tile = {item["tile"]: item for item in view["tile_tracker"]}

        self.assertEqual(by_tile["1m"]["known_count"], 1)
        self.assertEqual(by_tile["2m"]["known_count"], 1)
        self.assertEqual(by_tile["5p"]["known_count"], 1)
        self.assertEqual(by_tile["7s"]["known_count"], 3)
        self.assertEqual(by_tile["9m"]["known_count"], 0)


if __name__ == "__main__":
    unittest.main()
