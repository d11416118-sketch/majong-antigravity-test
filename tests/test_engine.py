import base64
import json
import os
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["AI_PROVIDER"] = "heuristic"

import app as app_module
from backend import visible_ai as visible_ai_module
from backend.accounts import AccountManager
from backend.game_engine import GameEngine, STATE_END_GAME, STATE_PLAYER_TURN, STATE_WAIT_RESPONSE
from backend.visible_ai import VisibleMahjongAI


class TestGameEngine(unittest.TestCase):
    @staticmethod
    def waiting_on_9m():
        return [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "5m", "5m", "5m",
            "9m",
        ]

    @staticmethod
    def one_meld_waiting_on_9m():
        return [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "9m",
        ]

    def make_priority_game(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.state = STATE_PLAYER_TURN
        game.current_turn_idx = 0
        return game

    def test_start_round_deals_taiwan_16_tiles(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")

        game.start_game()

        self.assertEqual(game.state, STATE_PLAYER_TURN)
        self.assertEqual(game.current_turn_idx, 0)
        self.assertEqual(len(game.players[0].hand), 17)
        self.assertEqual([len(player.hand) for player in game.players[1:]], [16, 16, 16])
        self.assertTrue(all(not tile.endswith("f") for player in game.players for tile in player.hand))

    def test_match_id_stays_stable_while_hand_id_changes(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")

        game.start_game()
        match_id = game.match.match_id
        first_hand_id = game.match.hand_id
        game.start_round()

        self.assertEqual(game.match.match_id, match_id)
        self.assertNotEqual(game.match.hand_id, first_hand_id)
        self.assertEqual(game.match.hand_number, 2)

    def test_snapshot_preserves_current_match_scores_for_reconnect(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.start_game()
        expected_scores = {"u0": 10, "u1": 5, "u2": 3, "u3": -18}
        game.match.cumulative_scores = dict(expected_scores)
        for player in game.players:
            player.score = expected_scores[player.uid]

        snapshot = game.get_snapshot("u2")

        self.assertEqual(snapshot["match"]["cumulative_scores"], expected_scores)
        self.assertEqual(
            {player["uid"]: player["score"] for player in snapshot["players"]},
            expected_scores,
        )

    def test_snapshot_names_the_player_who_must_discard(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.start_game()

        own_prompt = game.get_snapshot("u0")["turn_prompt"]
        other_prompt = game.get_snapshot("u1")["turn_prompt"]

        self.assertEqual(own_prompt["phase"], "DISCARD")
        self.assertEqual(own_prompt["actor_names"], ["P0"])
        self.assertTrue(own_prompt["is_recipient"])
        self.assertFalse(other_prompt["is_recipient"])

    def test_snapshot_names_only_players_still_waiting_to_respond(self):
        game = self.make_priority_game()
        actions = [
            {"uid": "u1", "type": "CHI", "tile": "3m", "tiles": ["1m", "2m"], "player_idx": 1},
            {"uid": "u2", "type": "PON", "tile": "3m", "player_idx": 2},
        ]
        game.state = STATE_WAIT_RESPONSE
        game.pending_uids = {"u1", "u2"}
        game.pending_actions_by_uid = game._group_actions_by_uid(actions)

        prompt = game.get_snapshot("u1")["turn_prompt"]
        self.assertEqual(prompt["actor_names"], ["P1", "P2"])
        self.assertTrue(prompt["is_recipient"])
        self.assertEqual(prompt["recipient_actions"], ["CHI"])

        result = game.submit_response("u1", "PASS")
        self.assertEqual(result["status"], "WAITING")
        waiting_prompt = game.get_snapshot("u0")["turn_prompt"]
        self.assertEqual(waiting_prompt["actor_names"], ["P2"])
        self.assertEqual(waiting_prompt["actor_uids"], ["u2"])

    def test_east_match_ends_after_each_seat_loses_dealer_once(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.start_game()

        for dealer_idx in range(4):
            self.assertEqual(game.dealer_idx, dealer_idx)
            game._update_dealer_after_round({(dealer_idx + 1) % 4})

        self.assertEqual(game.dealer_idx, 0)
        self.assertEqual(game.round_wind, 1)
        self.assertTrue(game._match_should_end())

    def test_dealer_win_and_draw_keep_the_dealer(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.start_game()

        game._update_dealer_after_round({0})
        self.assertEqual((game.dealer_idx, game.lian_zhuang), (0, 1))
        game._update_dealer_after_round(set())
        self.assertEqual((game.dealer_idx, game.lian_zhuang), (0, 2))

    def test_discard_sets_last_discard_or_waits_for_claim(self):
        game = GameEngine()
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.start_game()

        discarded_tile = game.players[0].hand[0]
        actions = game.discard_tile("u0", 0)

        self.assertEqual(game.last_discard["tile"], discarded_tile)
        self.assertIn(game.state, {STATE_PLAYER_TURN, STATE_WAIT_RESPONSE})
        if not actions:
            self.assertEqual(game.players[0].discards[-1], discarded_tile)

    def test_pon_beats_waiting_chi(self):
        game = self.make_priority_game()
        game.players[0].hand = ["5m"]
        game.players[1].hand = ["3m", "4m"]
        game.players[2].hand = ["5m", "5m"]

        actions = game.discard_tile("u0", 0)
        self.assertEqual({action["type"] for action in actions}, {"CHI", "PON"})

        result = game.submit_response("u2", "PON")

        self.assertEqual(result["status"], "ACTION")
        self.assertEqual(result["action"]["type"], "PON")
        self.assertEqual(result["action"]["uid"], "u2")

    def test_pon_waits_for_unanswered_hu(self):
        game = self.make_priority_game()
        game.players[0].hand = ["9m"]
        game.players[1].hand = [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "5m", "5m", "5m",
            "9m",
        ]
        game.players[2].hand = ["9m", "9m"]

        game.discard_tile("u0", 0)
        result = game.submit_response("u2", "PON")
        self.assertEqual(result["status"], "WAITING")

        result = game.submit_response("u1", "PASS")
        self.assertEqual(result["status"], "ACTION")
        self.assertEqual(result["action"]["type"], "PON")
        self.assertEqual(result["action"]["uid"], "u2")

    def test_hu_beats_waiting_pon(self):
        game = self.make_priority_game()
        game.players[0].hand = ["9m"]
        game.players[1].hand = [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "5m", "5m", "5m",
            "9m",
        ]
        game.players[2].hand = ["9m", "9m"]

        game.discard_tile("u0", 0)
        result = game.submit_response("u1", "HU")

        self.assertEqual(result["status"], "ACTION")
        self.assertEqual(result["action"]["type"], "HU")
        self.assertEqual(result["action"]["uid"], "u1")

    def test_first_hu_response_wins_immediately(self):
        game = self.make_priority_game()
        game.players[0].hand = ["9m"]
        game.players[1].hand = self.waiting_on_9m()
        game.players[2].hand = self.waiting_on_9m()

        game.discard_tile("u0", 0)
        result = game.submit_response("u2", "HU")

        self.assertEqual(result["status"], "ACTION")
        self.assertEqual(result["action"]["uid"], "u2")
        game.apply_claim_action(result["action"])
        self.assertEqual(game.submit_response("u1", "HU")["status"], "IGNORED")

    def test_guoshui_blocks_hu_and_clears_after_a_safe_draw_discard(self):
        game = self.make_priority_game()
        game.players[0].hand = ["9m"]
        game.players[1].hand = self.waiting_on_9m()

        game.discard_tile("u0", 0)
        result = game.submit_response("u1", "PASS")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(game.players[1].guoshui)

        game._enter_player_turn(2)
        game.players[2].hand = ["9m"]
        actions = game.discard_tile("u2", 0)
        self.assertFalse(any(action["type"] == "HU" and action["uid"] == "u1" for action in actions))

        game._enter_player_turn(1)
        game.wall = ["1p"]
        game._draw_until_play_tile(game.players[1], from_back=False, draw_context="NORMAL")
        self.assertTrue(game.players[1].guoshui_safe_draw)
        safe_tile_index = game.players[1].hand.index("1p")
        game.discard_tile("u1", safe_tile_index)
        self.assertFalse(game.players[1].guoshui)

        game._enter_player_turn(2)
        game.players[2].hand = ["9m"]
        actions = game.discard_tile("u2", 0)
        self.assertTrue(any(action["type"] == "HU" and action["uid"] == "u1" for action in actions))

    def test_guoshui_blocks_self_draw_until_safe_cycle(self):
        game = self.make_priority_game()
        player = game.players[0]
        player.hand = self.waiting_on_9m() + ["9m"]
        player.guoshui = True

        self.assertFalse(any(action["type"] == "HU" for action in game.check_self_actions()))
        with self.assertRaisesRegex(ValueError, "過水"):
            game.apply_self_action("u0", "HU", "9m")

    def test_passing_a_self_draw_marks_guoshui(self):
        game = self.make_priority_game()
        player = game.players[0]
        player.hand = self.waiting_on_9m() + ["9m"]

        game.discard_tile("u0", player.hand.index("9m"))

        self.assertTrue(player.guoshui)

    def test_declared_ting_locks_hand_and_di_ting_loses_bonus_after_guoshui(self):
        game = self.make_priority_game()
        player = game.players[0]
        player.hand = self.waiting_on_9m() + ["1p"]
        player.sort_hand()
        ting_option = next(
            option for option in game.ting_discard_options()
            if option["tile"] == "1p"
        )

        game.declare_ting("u0", ting_option["tile_index"])

        self.assertTrue(player.declared_ting)
        self.assertTrue(player.di_ting)
        self.assertTrue(player.di_ting_valid)
        declared_actions = game.check_actions_for_discard("5m", 1)
        self.assertFalse(any(action["type"] in {"CHI", "PON"} for action in declared_actions))
        self.assertTrue(any(action["type"] == "KANG" for action in declared_actions))

        game._mark_guoshui(player)
        self.assertFalse(player.di_ting_valid)
        self.assertTrue(player.declared_ting)

        game._enter_player_turn(0)
        player.add_tile("2p")
        game.last_drawn_tile = "2p"
        with self.assertRaisesRegex(ValueError, "摸什麼打什麼"):
            game.discard_tile("u0", 0)

    def test_di_ting_ignores_other_players_claims_and_declared_ting_forces_bukang(self):
        claimed_game = self.make_priority_game()
        claimed_player = claimed_game.players[0]
        claimed_player.hand = self.waiting_on_9m() + ["1p"]
        claimed_player.sort_hand()
        claimed_game.opening_claim_occurred = True
        option = next(
            item for item in claimed_game.ting_discard_options()
            if item["tile"] == "1p"
        )
        claimed_game.declare_ting("u0", option["tile_index"])
        self.assertTrue(claimed_player.declared_ting)
        self.assertTrue(claimed_player.di_ting)

        forced_game = self.make_priority_game()
        forced_player = forced_game.players[0]
        forced_player.declared_ting = True
        forced_player.hand = ["5m"]
        forced_player.melds = [
            {"type": "PON", "tile": "5m", "tiles": ["5m"] * 3, "from_idx": 3}
        ]
        forced_game.wall = ["9p"]
        room_id = "forced-ting-bukang"
        app_module.rooms[room_id] = {
            "state": "GAME",
            "game": forced_game,
        }
        try:
            result = app_module.maybe_apply_declared_ting_bukang(
                room_id,
                {"status": "DRAW"},
            )
        finally:
            app_module.rooms.pop(room_id, None)

        self.assertEqual(result["status"], "SELF_ACTION")
        self.assertEqual(forced_player.melds[0]["type"], "BUKANG")
        self.assertNotIn("5m", forced_player.hand)
        self.assertEqual(forced_game.last_replacement_source, "BUKANG")

    def test_baxian_and_qiqiangyi_use_different_payers(self):
        baxian = self.make_priority_game()
        baxian.players[0].flowers = [f"{rank}f" for rank in range(1, 9)]
        baxian._update_flower_win_candidate(baxian.players[0], initial=False)
        baxian_result = baxian.resolve_pending_flower_win()
        baxian_payload = baxian_result["payload"]

        self.assertTrue(baxian_payload["special_flags"]["baxian"])
        self.assertEqual(set(baxian_payload["payment_units_by_uid"]), {"u1", "u2", "u3"})
        self.assertTrue(all(value == baxian_payload["score_breakdown"]["total"] for value in baxian_payload["payment_units_by_uid"].values()))
        self.assertEqual(sum(baxian_payload["score_deltas"].values()), 0)

        qiqiang = self.make_priority_game()
        qiqiang.players[0].flowers = [f"{rank}f" for rank in range(1, 8)]
        qiqiang.players[1].flowers = ["8f"]
        qiqiang._update_flower_win_candidate(qiqiang.players[1], initial=False)
        qiqiang_result = qiqiang.resolve_pending_flower_win()
        qiqiang_payload = qiqiang_result["payload"]

        self.assertTrue(qiqiang_payload["special_flags"]["qiqiangyi"])
        self.assertEqual(qiqiang_payload["flower_payer_uid"], "u1")
        self.assertEqual(set(qiqiang_payload["payment_units_by_uid"]), {"u1"})
        self.assertEqual(qiqiang_payload["score_deltas"]["u2"], 0)
        self.assertEqual(qiqiang_payload["score_deltas"]["u3"], 0)
        self.assertEqual(sum(qiqiang_payload["score_deltas"].values()), 0)

    def test_bukang_opens_qiang_gang_window(self):
        game = self.make_priority_game()
        game.players[0].hand = ["5m"]
        game.players[0].melds = [{"type": "PON", "tile": "5m", "tiles": ["5m"] * 3, "from_idx": 3}]
        game.players[1].hand = [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "6m", "7m", "8m",
            "5m",
        ]

        pending = game.apply_self_action("u0", "BUKANG", "5m")
        self.assertEqual(pending["status"], "WAIT_RESPONSE")
        self.assertEqual(game.players[0].melds[0]["type"], "PON")
        self.assertIn("5m", game.players[0].hand)

        response = game.submit_response("u1", "HU")
        result = game.apply_claim_action(response["action"])
        names = [item["name"] for item in result["payload"]["score_breakdown"]["breakdown"]]

        self.assertIn("搶槓", names)
        self.assertEqual(result["payload"]["payer_uid"], "u0")

    def test_bukang_completes_after_every_qiang_gang_player_passes(self):
        game = self.make_priority_game()
        game.players[0].hand = ["5m"]
        game.players[0].melds = [{"type": "PON", "tile": "5m", "tiles": ["5m"] * 3, "from_idx": 3}]
        game.players[1].hand = [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "6m", "7m", "8m",
            "5m",
        ]
        game.wall = ["9p"]

        pending = game.apply_self_action("u0", "BUKANG", "5m")
        self.assertEqual(pending["status"], "WAIT_RESPONSE")
        result = game.submit_response("u1", "PASS")

        self.assertEqual(result["status"], "SELF_ACTION")
        self.assertEqual(game.players[0].melds[0]["type"], "BUKANG")
        self.assertNotIn("5m", game.players[0].hand)
        self.assertEqual(game.last_draw_context, "REPLACEMENT")
        self.assertTrue(game.players[1].guoshui)

    def test_haidi_and_hedi_are_detected_from_the_last_live_tile(self):
        haidi_game = self.make_priority_game()
        haidi_game.players[0].hand = self.waiting_on_9m()
        haidi_game.wall = ["9m"] + ["1p"] * haidi_game.rules.dead_wall_size
        haidi_game.total_discard_count = 5
        haidi_game.opening_claim_occurred = True
        haidi_game._draw_until_play_tile(haidi_game.players[0], from_back=False, draw_context="NORMAL")
        haidi_result = haidi_game.apply_self_action("u0", "HU", "9m")
        haidi_names = [item["name"] for item in haidi_result["payload"]["score_breakdown"]["breakdown"]]
        self.assertIn("海底撈月", haidi_names)

        hedi_game = self.make_priority_game()
        hedi_game.players[0].hand = ["9m"]
        hedi_game.players[1].hand = self.waiting_on_9m()
        hedi_game.last_draw_context = "NORMAL"
        hedi_game.last_draw_is_haidi = True
        hedi_game.total_discard_count = 5
        hedi_game.opening_claim_occurred = True
        actions = hedi_game.discard_tile("u0", 0)
        hu = next(action for action in actions if action["type"] == "HU")
        response = hedi_game.submit_response(hu["uid"], "HU")
        hedi_result = hedi_game.apply_claim_action(response["action"])
        hedi_names = [item["name"] for item in hedi_result["payload"]["score_breakdown"]["breakdown"]]
        self.assertIn("河底撈魚", hedi_names)

    def test_replacement_draw_hu_is_gang_shang_kai_hua(self):
        game = self.make_priority_game()
        game.players[0].hand = self.waiting_on_9m()
        game.wall = ["9m"]
        game.total_discard_count = 2
        game.opening_claim_occurred = True

        game.draw_replacement_tile(game.players[0], source="ANKANG")
        result = game.apply_self_action("u0", "HU", "9m")
        names = [item["name"] for item in result["payload"]["score_breakdown"]["breakdown"]]

        self.assertIn("槓上開花", names)

    def test_declared_ting_mingkang_cannot_self_draw_even_after_flower(self):
        game = self.make_priority_game()
        game.players[0].hand = ["5s"]
        player = game.players[1]
        player.declared_ting = True
        player.hand = self.one_meld_waiting_on_9m() + ["5s"] * 3
        game.wall = ["9m", "1f"]

        actions = game.discard_tile("u0", 0)
        kang = next(
            action
            for action in actions
            if action["uid"] == "u1" and action["type"] == "KANG"
        )
        game.apply_claim_action(kang)

        self.assertEqual(game.last_draw_context, "REPLACEMENT")
        self.assertEqual(game.last_replacement_source, "MINGKANG")
        self.assertIn("1f", player.flowers)
        self.assertFalse(any(action["type"] == "HU" for action in game.check_self_actions()))
        with self.assertRaisesRegex(ValueError, "明槓補牌不可自摸"):
            game.apply_self_action("u1", "HU", "9m")

    def test_declared_ting_ankang_and_bukang_replacements_can_self_draw(self):
        ankang_game = self.make_priority_game()
        ankang_player = ankang_game.players[0]
        ankang_player.declared_ting = True
        ankang_player.hand = self.one_meld_waiting_on_9m() + ["5s"] * 4
        ankang_game.wall = ["9m"]

        self.assertTrue(
            any(
                action["type"] == "ANKANG" and action["tile"] == "5s"
                for action in ankang_game.check_self_actions()
            )
        )
        ankang_game.apply_self_action("u0", "ANKANG", "5s")
        self.assertEqual(ankang_game.last_replacement_source, "ANKANG")
        self.assertTrue(any(action["type"] == "HU" for action in ankang_game.check_self_actions()))

        bukang_game = self.make_priority_game()
        bukang_player = bukang_game.players[0]
        bukang_player.declared_ting = True
        bukang_player.hand = self.one_meld_waiting_on_9m() + ["5s"]
        bukang_player.melds = [
            {"type": "PON", "tile": "5s", "tiles": ["5s"] * 3, "from_idx": 3}
        ]
        bukang_game.wall = ["9m"]

        bukang_game.apply_self_action("u0", "BUKANG", "5s")
        self.assertEqual(bukang_game.last_replacement_source, "BUKANG")
        self.assertTrue(any(action["type"] == "HU" for action in bukang_game.check_self_actions()))

    def test_tian_di_and_ren_hu_are_detected(self):
        tian_game = self.make_priority_game()
        tian_game.players[0].hand = self.waiting_on_9m() + ["9m"]
        tian = tian_game.apply_self_action("u0", "HU", "9m")
        tian_names = [item["name"] for item in tian["payload"]["score_breakdown"]["breakdown"]]
        self.assertIn("天胡", tian_names)
        self.assertNotIn("自摸", tian_names)
        self.assertNotIn("門清", tian_names)

        di_game = self.make_priority_game()
        di_game.current_turn_idx = 1
        di_game.players[1].hand = self.waiting_on_9m() + ["9m"]
        di_game.normal_draw_counts["u1"] = 1
        di_game.total_discard_count = 1
        di_game.opening_claim_occurred = True
        di = di_game.apply_self_action("u1", "HU", "9m")
        di_names = [item["name"] for item in di["payload"]["score_breakdown"]["breakdown"]]
        self.assertIn("地胡", di_names)
        self.assertNotIn("自摸", di_names)
        self.assertNotIn("門清", di_names)

        ren_game = self.make_priority_game()
        ren_game.players[0].hand = ["9m"]
        ren_game.players[1].hand = self.waiting_on_9m()
        ren_game.normal_draw_counts["u1"] = 1
        ren_game.opening_claim_occurred = True
        ren_game.discard_tile("u0", 0)
        response = ren_game.submit_response("u1", "HU")
        ren = ren_game.apply_claim_action(response["action"])
        ren_names = [item["name"] for item in ren["payload"]["score_breakdown"]["breakdown"]]
        self.assertIn("人胡", ren_names)
        self.assertNotIn("門清", ren_names)

    def test_nondealer_zimo_charges_dealer_and_lian_zhuang_bonus_only_to_dealer(self):
        game = self.make_priority_game()
        game.current_turn_idx = 1
        game.lian_zhuang = 1
        game.players[1].hand = self.waiting_on_9m() + ["9m"]
        game.normal_draw_counts["u1"] = 2
        game.total_discard_count = 5
        game.opening_claim_occurred = True

        result = game.apply_self_action("u1", "HU", "9m")
        payload = result["payload"]
        normal_payment = payload["score_breakdown"]["total"]

        self.assertEqual(payload["payment_units_by_uid"]["u0"], normal_payment + 3)
        self.assertEqual(payload["payment_units_by_uid"]["u2"], normal_payment)
        self.assertEqual(payload["payment_units_by_uid"]["u3"], normal_payment)
        self.assertEqual(payload["score_deltas"]["u0"], -(normal_payment + 3))
        self.assertEqual(sum(payload["score_deltas"].values()), 0)

    def test_rejects_unavailable_response(self):
        game = self.make_priority_game()
        game.players[0].hand = ["5m"]
        game.players[1].hand = ["3m", "4m"]

        game.discard_tile("u0", 0)

        with self.assertRaises(ValueError):
            game.submit_response("u1", "HU")

    def test_visible_ai_can_choose_claim_actions(self):
        game = GameEngine()
        game.add_player("ai", "AI")
        game.players[0].hand = ["3m", "4m", "7p", "7p", "9s", "9s", "9s"]

        hu = VisibleMahjongAI.choose_response(game, "ai", [{"uid": "ai", "type": "HU", "tile": "5m"}], use_api=False)
        self.assertEqual(hu["type"], "HU")

        kong = VisibleMahjongAI.choose_response(
            game,
            "ai",
            [
                {"uid": "ai", "type": "PON", "tile": "9s"},
                {"uid": "ai", "type": "KANG", "tile": "9s"},
            ],
            use_api=False,
        )
        self.assertEqual(kong["type"], "KANG")

        pon = VisibleMahjongAI.choose_response(game, "ai", [{"uid": "ai", "type": "PON", "tile": "7p"}], use_api=False)
        self.assertEqual(pon["type"], "PON")

        chi = VisibleMahjongAI.choose_response(
            game,
            "ai",
            [{"uid": "ai", "type": "CHI", "tile": "5m", "tiles": ["3m", "4m"]}],
            use_api=False,
        )
        self.assertEqual(chi["type"], "CHI")
        self.assertEqual(chi["tiles"], ["3m", "4m"])

    def test_visible_ai_discards_isolated_honor_before_good_shape(self):
        game = GameEngine()
        game.add_player("ai", "AI")
        game.players[0].hand = [
            "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m",
            "2p", "3p", "4p", "5p", "5p",
            "7s", "8s", "9s",
            "1z",
        ]

        tile_index = VisibleMahjongAI.choose_discard(game, 0, use_api=False)

        self.assertEqual(game.players[0].hand[tile_index], "1z")

    def test_visible_ai_discard_does_not_read_hidden_opponent_hands(self):
        hand = [
            "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m",
            "2p", "3p", "4p", "5p", "5p",
            "7s", "8s", "9s",
            "1z",
        ]

        first = GameEngine()
        second = GameEngine()
        for game in (first, second):
            for idx in range(4):
                game.add_player(f"u{idx}", f"P{idx}")
            game.players[0].hand = list(hand)

        for player in first.players[1:]:
            player.hand = ["1m"] * 16
        for player in second.players[1:]:
            player.hand = ["9s"] * 16

        first_choice = VisibleMahjongAI.choose_discard(first, 0, use_api=False)
        second_choice = VisibleMahjongAI.choose_discard(second, 0, use_api=False)

        self.assertEqual(first_choice, second_choice)
        self.assertEqual(hand[first_choice], "1z")

    def test_visible_ai_passes_claims_that_do_not_improve_hand(self):
        game = GameEngine()
        game.add_player("ai", "AI")
        game.players[0].hand = [
            "2m", "3m", "4m", "5m", "6m", "7m",
            "2p", "3p", "4p", "6p", "7p", "8p",
            "2s", "3s", "4s",
            "9s",
        ]

        action = VisibleMahjongAI.choose_response(
            game,
            "ai",
            [{"uid": "ai", "type": "PON", "tile": "9s"}],
            use_api=False,
        )

        self.assertEqual(action["type"], "PASS")

    def test_ollama_request_uses_local_chat_api_and_two_minute_timeout(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return {"message": {"content": '{"action":"DISCARD","tile_index":3}'}}

        with patch.dict(
            os.environ,
            {
                "OLLAMA_API_BASE": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:4b",
                "OLLAMA_TIMEOUT_SECONDS": "120",
            },
        ), patch.object(visible_ai_module, "_urlopen_json", side_effect=fake_urlopen):
            result = visible_ai_module._call_ollama({"task": "choose_discard"})

        self.assertEqual(result, {"action": "DISCARD", "tile_index": 3})
        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(captured["timeout"], 120.0)
        self.assertEqual(captured["body"]["model"], "qwen3:4b")
        self.assertFalse(captured["body"]["stream"])
        self.assertFalse(captured["body"]["think"])
        self.assertEqual(captured["body"]["format"]["type"], "object")
        self.assertEqual(captured["body"]["format"]["required"], ["action"])
        self.assertFalse(captured["body"]["format"]["additionalProperties"])

    def test_ollama_failure_falls_back_to_python_heuristic(self):
        game = GameEngine()
        game.add_player("ai", "AI")
        game.players[0].hand = [
            "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m",
            "2p", "3p", "4p", "5p", "5p",
            "7s", "8s", "9s",
            "1z",
        ]
        expected = VisibleMahjongAI.choose_discard(game, 0, use_api=False)

        with patch.dict(os.environ, {"AI_PROVIDER": "ollama"}), patch.object(
            visible_ai_module,
            "_call_ollama",
            return_value=None,
        ):
            actual = VisibleMahjongAI.choose_discard(game, 0, use_api=True)

        self.assertEqual(actual, expected)

    def test_ai_prompt_contains_approved_house_rules(self):
        instruction = visible_ai_module._system_instruction()
        self.assertIn("first HU response", instruction)
        self.assertIn("Guoshui", instruction)
        self.assertIn("Di-Ting", instruction)
        self.assertIn("MingKang", instruction)
        self.assertIn("AnKang", instruction)
        self.assertIn("BuKang", instruction)


class TestSocketRooms(unittest.TestCase):
    def setUp(self):
        self.old_account_manager = app_module.account_manager
        self.test_db_dir = tempfile.TemporaryDirectory()
        app_module.account_manager = AccountManager(os.path.join(self.test_db_dir.name, "accounts.db"))
        app_module.rooms.clear()
        app_module.rooms_by_uid.clear()
        app_module.socket_accounts.clear()
        app_module.matchmaking_queue.clear()
        app_module.online_sessions.clear()

    def tearDown(self):
        app_module.rooms.clear()
        app_module.rooms_by_uid.clear()
        app_module.socket_accounts.clear()
        app_module.matchmaking_queue.clear()
        app_module.online_sessions.clear()
        app_module.account_manager = self.old_account_manager
        self.test_db_dir.cleanup()

    def auth_client(self, client, label):
        username = f"{label}_{uuid.uuid4().hex[:8]}"
        client.emit("register_account", {"username": username, "password": "secret123"})
        received = client.get_received()
        auth = next(event for event in received if event["name"] == "auth_success")
        return auth["args"][0]

    def test_four_players_auto_start(self):
        clients = [app_module.socketio.test_client(app_module.app) for _ in range(4)]
        for idx, client in enumerate(clients):
            self.auth_client(client, f"P{idx}")

        clients[0].emit("create_room", {})
        created = next(event for event in clients[0].get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]

        for idx in range(1, 4):
            clients[idx].emit("join_room", {"room_id": room_id})

        self.assertEqual(app_module.rooms[room_id]["state"], "GAME")
        self.assertEqual(len(app_module.rooms[room_id]["game"].players), 4)

        for client in clients:
            client.disconnect()

    def test_custom_room_keeps_owner_scoring_settings(self):
        client = app_module.socketio.test_client(app_module.app)
        auth = self.auth_client(client, "Owner")

        client.emit("create_room", {"track_stats": True, "base_stake": 25})
        created = next(event for event in client.get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]
        room = app_module.rooms[room_id]

        self.assertEqual(room["mode"], "custom")
        self.assertEqual(room["host_uid"], auth["account"]["id"])
        self.assertTrue(room["track_stats"])
        self.assertEqual(room["base_stake"], 25)

        client.disconnect()

    def test_theme_selection_is_shared_as_safe_room_metadata(self):
        host = app_module.socketio.test_client(app_module.app)
        guest = app_module.socketio.test_client(app_module.app)
        host_auth = self.auth_client(host, "ThemeHost")
        self.auth_client(guest, "ThemeGuest")

        host.emit("create_room", {})
        created = next(event for event in host.get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]
        guest.emit("join_room", {"room_id": room_id})
        host.get_received()
        guest.get_received()

        host.emit("update_theme", {"theme_id": "cyber"})
        host_events = host.get_received()
        guest_events = guest.get_received()
        profile = next(event for event in host_events if event["name"] == "profile_update")["args"][0]
        lobby = [event for event in guest_events if event["name"] == "update_lobby"][-1]["args"][0]
        host_uid = host_auth["account"]["id"]
        public_host = next(player for player in lobby["players"] if player["uid"] == host_uid)

        self.assertEqual(profile["theme_id"], "cyber")
        self.assertEqual(public_host["theme_id"], "cyber")
        self.assertEqual(app_module.rooms[room_id]["players"][0]["theme_id"], "cyber")
        snapshot_host = next(
            player
            for player in app_module.enriched_snapshot(room_id, host_uid)["players"]
            if player["uid"] == host_uid
        )
        self.assertEqual(snapshot_host["theme_id"], "cyber")

        host.disconnect()
        guest.disconnect()

    def test_matchmaking_starts_room_with_four_real_players(self):
        clients = [app_module.socketio.test_client(app_module.app) for _ in range(4)]
        for idx, client in enumerate(clients):
            self.auth_client(client, f"Queue{idx}")
            client.get_received()

        for client in clients:
            client.emit("join_matchmaking", {})

        self.assertEqual(len(app_module.matchmaking_queue), 0)
        matched_rooms = [room for room in app_module.rooms.values() if room["mode"] == "matchmaking"]
        self.assertEqual(len(matched_rooms), 1)
        self.assertEqual(matched_rooms[0]["state"], "GAME")
        self.assertTrue(matched_rooms[0]["track_stats"])
        self.assertEqual(len(matched_rooms[0]["players"]), 4)

        for client in clients:
            client.disconnect()

    def test_ranked_queue_starts_ranked_room(self):
        clients = [app_module.socketio.test_client(app_module.app) for _ in range(4)]
        for idx, client in enumerate(clients):
            self.auth_client(client, f"Rank{idx}")
            client.get_received()

        for client in clients:
            client.emit("join_ranked", {})

        self.assertEqual(len(app_module.matchmaking_queue), 0)
        ranked_rooms = [room for room in app_module.rooms.values() if room["mode"] == "ranked"]
        self.assertEqual(len(ranked_rooms), 1)
        self.assertEqual(ranked_rooms[0]["state"], "GAME")
        self.assertTrue(ranked_rooms[0]["track_stats"])
        self.assertEqual(len(ranked_rooms[0]["players"]), 4)
        self.assertTrue(all(player["rank_name"] == "一段" for player in ranked_rooms[0]["players"]))

        for client in clients:
            client.disconnect()

    def test_online_reward_ping_grants_after_interval(self):
        client = app_module.socketio.test_client(app_module.app)
        auth = self.auth_client(client, "Online")
        account_id = auth["account"]["id"]
        client.get_received()

        app_module.online_sessions[account_id]["last_reward_at"] -= app_module.ONLINE_REWARD_SECONDS
        client.emit("online_reward_ping", {})
        received = client.get_received()

        self.assertTrue(any(event["name"] == "reward_claimed" for event in received))
        self.assertEqual(app_module.account_manager.get_profile(account_id)["coins"], 15)
        client.disconnect()

    def test_play_reward_grants_once_per_round(self):
        old_manager = app_module.account_manager
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
                app_module.account_manager = manager
                accounts = [manager.register(f"Play{idx}", "secret123") for idx in range(4)]

                room_id = app_module.create_room_state("custom", None, False, 10)
                room = app_module.rooms[room_id]
                for account in accounts:
                    room["players"].append({"uid": account["id"], "name": account["username"], "connected": True, "ai_enabled": False})

                app_module.grant_play_rewards(room_id)
                app_module.grant_play_rewards(room_id)

                self.assertEqual(manager.get_profile(accounts[0]["id"])["coins"], 11)
        finally:
            app_module.account_manager = old_manager

    def test_socket_friend_request_accept_flow(self):
        alice_client = app_module.socketio.test_client(app_module.app)
        bob_client = app_module.socketio.test_client(app_module.app)
        alice = self.auth_client(alice_client, "FriendA")
        bob = self.auth_client(bob_client, "FriendB")
        alice_client.get_received()
        bob_client.get_received()

        alice_client.emit("send_friend_request", {"username": bob["account"]["username"]})
        bob_events = bob_client.get_received()
        bob_social = next(event for event in bob_events if event["name"] == "social_state")["args"][0]
        self.assertEqual(bob_social["incoming"][0]["account_id"], alice["account"]["id"])

        bob_client.emit("respond_friend_request", {"requester_id": alice["account"]["id"], "accept": True})
        alice_events = alice_client.get_received()
        alice_social = [event for event in alice_events if event["name"] == "social_state"][-1]["args"][0]
        self.assertEqual(alice_social["friends"][0]["account_id"], bob["account"]["id"])

        alice_client.disconnect()
        bob_client.disconnect()

    def test_match_result_records_once_with_all_hands_and_final_score(self):
        old_manager = app_module.account_manager
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
                app_module.account_manager = manager
                accounts = [manager.register(f"Hist{idx}", "secret123") for idx in range(4)]

                room_id = app_module.create_room_state("matchmaking", None, True, 10)
                room = app_module.rooms[room_id]
                game = room["game"]
                for account in accounts:
                    game.add_player(account["id"], account["username"])
                    room["players"].append({"uid": account["id"], "name": account["username"], "connected": True, "ai_enabled": False})

                game.start_game()
                app_module.prepare_match_tracking(room_id)
                game.match.record_hand({"hand_id": game.match.hand_id, "result": "HU", "winner_uid": accounts[0]["id"]})
                game.match.apply_score_deltas(
                    {
                        accounts[0]["id"]: 6,
                        accounts[1]["id"]: -2,
                        accounts[2]["id"]: -2,
                        accounts[3]["id"]: -2,
                    }
                )
                game.match.finish({player.uid: player.idx for player in game.players})
                app_module.finalize_match_result(room_id)
                app_module.finalize_match_result(room_id)

                history = manager.get_recent_history(accounts[0]["id"])
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["match_id"], game.match.match_id)
                self.assertEqual(history[0]["score_delta"], 6)
                self.assertEqual(history[0]["coin_delta"], 60)
                self.assertEqual(history[0]["round_detail"]["hand_count"], 1)
                self.assertEqual(len(history[0]["round_detail"]["hands"]), 1)
        finally:
            app_module.account_manager = old_manager

    def test_ranked_points_are_applied_only_after_the_match_ends(self):
        old_manager = app_module.account_manager
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
                app_module.account_manager = manager
                accounts = [manager.register(f"RankHist{idx}", "secret123") for idx in range(4)]

                room_id = app_module.create_room_state("ranked", None, True, 10)
                room = app_module.rooms[room_id]
                game = room["game"]
                for account in accounts:
                    game.add_player(account["id"], account["username"])
                    room["players"].append({"uid": account["id"], "name": account["username"], "connected": True, "ai_enabled": False})

                game.start_game()
                app_module.prepare_match_tracking(room_id)
                game.match.record_hand({"hand_id": game.match.hand_id, "result": "HU", "winner_uid": accounts[0]["id"]})
                app_module.record_round_result(room_id, {})
                self.assertEqual(manager.get_profile(accounts[0]["id"])["rank_points"], 0)
                self.assertEqual(manager.get_recent_history(accounts[0]["id"]), [])

                game.match.apply_score_deltas(
                    {
                        accounts[0]["id"]: 3,
                        accounts[1]["id"]: 1,
                        accounts[2]["id"]: -1,
                        accounts[3]["id"]: -3,
                    }
                )
                game.match.finish({player.uid: player.idx for player in game.players})
                app_module.finalize_match_result(room_id)
                self.assertEqual(manager.get_profile(accounts[0]["id"])["rank_points"], 30)
                self.assertEqual(manager.get_profile(accounts[1]["id"])["rank_points"], 10)
                history = manager.get_recent_history(accounts[0]["id"])
                self.assertEqual(history[0]["mode"], "ranked")
                self.assertEqual(history[0]["rank_delta"], 30)
                self.assertEqual(history[0]["rank_points_after"], 30)
        finally:
            app_module.account_manager = old_manager

    def test_all_players_can_start_next_round(self):
        clients = [app_module.socketio.test_client(app_module.app) for _ in range(4)]
        for idx, client in enumerate(clients):
            self.auth_client(client, f"Next{idx}")

        clients[0].emit("create_room", {})
        created = next(event for event in clients[0].get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]

        for idx in range(1, 4):
            clients[idx].emit("join_room", {"room_id": room_id})

        room = app_module.rooms[room_id]
        game = room["game"]
        game.dealer_idx = 2
        game.lian_zhuang = 1
        game.state = STATE_END_GAME
        room["state"] = app_module.ROOM_HAND_ENDED
        room["next_round_votes"] = set()

        for client in clients:
            client.emit("request_next_round", {})

        self.assertEqual(room["state"], "GAME")
        self.assertEqual(game.state, STATE_PLAYER_TURN)
        self.assertEqual(game.current_turn_idx, 2)
        self.assertEqual(game.lian_zhuang, 1)
        self.assertEqual(len(game.players[2].hand), 17)

        for client in clients:
            client.disconnect()

    def test_room_chat_supports_text_stickers_and_images(self):
        host = app_module.socketio.test_client(app_module.app)
        guest = app_module.socketio.test_client(app_module.app)
        self.auth_client(host, "ChatHost")
        self.auth_client(guest, "ChatGuest")

        host.emit("create_room", {})
        created = next(event for event in host.get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]
        guest.emit("join_room", {"room_id": room_id})
        host.get_received()
        guest.get_received()

        host.emit("chat_send", {"kind": "text", "text": "大家好"})
        guest_events = guest.get_received()
        text_message = next(event for event in guest_events if event["name"] == "chat_message")["args"][0]
        self.assertEqual(text_message["kind"], "text")
        self.assertEqual(text_message["text"], "大家好")
        host.get_received()

        guest.emit("chat_send", {"kind": "sticker", "sticker_id": "hu"})
        sticker_events = host.get_received()
        sticker_message = next(event for event in sticker_events if event["name"] == "chat_message")["args"][0]
        self.assertEqual(sticker_message["kind"], "sticker")
        self.assertEqual(sticker_message["sticker_label"], "胡了")

        guest.emit(
            "chat_send",
            {
                "kind": "image",
                "image": {
                    "data_url": "data:image/png;base64,cG5n",
                    "name": "tiny.png",
                },
            },
        )
        image_events = host.get_received()
        image_message = next(event for event in image_events if event["name"] == "chat_message")["args"][0]
        self.assertEqual(image_message["kind"], "image")
        self.assertEqual(image_message["image"]["mime_type"], "image/png")
        self.assertEqual(image_message["image"]["size"], 3)

        host.disconnect()
        guest.disconnect()

    def test_room_chat_image_limit_allows_ten_mb_payloads(self):
        self.assertEqual(app_module.CHAT_MAX_IMAGE_MB, 10)
        self.assertEqual(app_module.CHAT_MAX_IMAGE_BYTES, 10 * 1024 * 1024)
        encoded_length = ((app_module.CHAT_MAX_IMAGE_BYTES + 2) // 3) * 4
        self.assertGreaterEqual(
            app_module.socketio.server.eio.max_http_buffer_size,
            app_module.CHAT_SOCKET_BUFFER_BYTES,
        )
        self.assertGreater(app_module.socketio.server.eio.max_http_buffer_size, encoded_length)

    def test_room_chat_image_validator_rejects_over_configured_limit(self):
        old_limit = app_module.CHAT_MAX_IMAGE_BYTES
        try:
            app_module.CHAT_MAX_IMAGE_BYTES = 3
            accepted = app_module.validate_chat_image(
                {
                    "data_url": "data:image/png;base64,cG5n",
                    "name": "tiny.png",
                }
            )
            self.assertEqual(accepted["size"], 3)

            too_large = base64.b64encode(b"toolong").decode("ascii")
            with self.assertRaises(ValueError):
                app_module.validate_chat_image(
                    {
                        "data_url": f"data:image/png;base64,{too_large}",
                        "name": "too-large.png",
                    }
                )
        finally:
            app_module.CHAT_MAX_IMAGE_BYTES = old_limit

    def test_room_chat_rejects_unknown_sticker(self):
        client = app_module.socketio.test_client(app_module.app)
        self.auth_client(client, "BadSticker")

        client.emit("create_room", {})
        next(event for event in client.get_received() if event["name"] == "room_created")
        client.emit("chat_send", {"kind": "sticker", "sticker_id": "missing"})
        received = client.get_received()

        self.assertTrue(any(event["name"] == "server_error" for event in received))
        client.disconnect()

    def test_ai_control_uses_only_explicit_ai_proxy(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        room["game"].add_player("u1", "Player")
        room["players"].append(
            {
                "uid": "u1",
                "name": "Player",
                "connected": False,
                "ai_enabled": False,
            }
        )

        self.assertFalse(app_module.is_ai_controlled(room_id, "u1"))

        app_module.set_player_ai_enabled(room_id, "u1", True)
        self.assertTrue(app_module.is_ai_controlled(room_id, "u1"))

    def test_disabling_ai_proxy_cancels_ai_task(self):
        client = app_module.socketio.test_client(app_module.app)
        auth = self.auth_client(client, "DisableAi")

        client.emit("create_room", {})
        created = next(event for event in client.get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]
        uid = auth["account"]["id"]
        app_module.set_player_ai_enabled(room_id, uid, True)
        app_module.rooms[room_id]["ai_busy"] = True

        client.emit("set_ai_enabled", {"enabled": False})

        player = next(player for player in app_module.rooms[room_id]["players"] if player["uid"] == uid)
        self.assertTrue(player["connected"])
        self.assertFalse(player["ai_enabled"])
        self.assertFalse(app_module.rooms[room_id]["ai_busy"])
        client.disconnect()

    def test_decision_timeout_randomly_discards_without_enabling_ai(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        game = room["game"]
        for idx in range(4):
            uid = f"u{idx}"
            game.add_player(uid, f"P{idx}")
            room["players"].append(
                {
                    "uid": uid,
                    "name": f"P{idx}",
                    "connected": True,
                    "ai_enabled": False,
                }
            )
        room["state"] = "GAME"
        game.start_game()
        active_player = game.players[game.current_turn_idx]
        before_hand = list(active_player.hand)
        active_uid = active_player.uid
        room["decision_seq"] = 7

        old_timeout = app_module.DECISION_TIMEOUT_SECONDS
        old_schedule = app_module.schedule_decision_timeout
        old_choice = app_module.random.choice
        try:
            app_module.DECISION_TIMEOUT_SECONDS = 0
            app_module.schedule_decision_timeout = lambda _room_id: None
            app_module.random.choice = lambda values: values[0]
            app_module.run_decision_timeout(room_id, 7)
        finally:
            app_module.DECISION_TIMEOUT_SECONDS = old_timeout
            app_module.schedule_decision_timeout = old_schedule
            app_module.random.choice = old_choice

        player = next(player for player in room["players"] if player["uid"] == active_uid)
        self.assertFalse(player["ai_enabled"])
        self.assertFalse(player.get("auto_play_enabled", False))
        self.assertEqual(len(active_player.hand), len(before_hand) - 1)
        self.assertEqual(active_player.discards[-1], before_hand[0])
        self.assertIsNone(room["decision_deadline"])

    def test_requested_thinking_and_offline_timeouts(self):
        self.assertEqual(app_module.DECISION_TIMEOUT_SECONDS, 50)
        self.assertEqual(app_module.OFFLINE_TIMEOUT_SECONDS, 150)
        self.assertEqual(app_module.AI_PROXY_DECISION_TIMEOUT_SECONDS, 120)

    def test_ollama_ai_proxy_gets_two_minute_decision_window(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        game = room["game"]
        for idx in range(4):
            uid = f"u{idx}"
            game.add_player(uid, f"P{idx}")
            room["players"].append(
                {
                    "uid": uid,
                    "name": f"P{idx}",
                    "connected": True,
                    "ai_enabled": False,
                    "auto_play_enabled": False,
                }
            )
        room["state"] = "GAME"
        game.start_game()
        active_uid = game.players[game.current_turn_idx].uid
        app_module.set_player_ai_enabled(room_id, active_uid, True)

        with patch.dict(
            os.environ,
            {"AI_PROVIDER": "ollama", "OLLAMA_TIMEOUT_SECONDS": "120"},
        ):
            timeout_seconds = app_module.decision_timeout_seconds_for_room(room_id)

        self.assertEqual(timeout_seconds, 120)

    def test_ollama_timeout_uses_python_fallback_and_cancels_stale_model(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        game = room["game"]
        for idx in range(4):
            uid = f"u{idx}"
            game.add_player(uid, f"P{idx}")
            room["players"].append(
                {
                    "uid": uid,
                    "name": f"P{idx}",
                    "connected": True,
                    "ai_enabled": False,
                    "auto_play_enabled": False,
                }
            )
        room["state"] = "GAME"
        game.start_game()
        player = game.players[game.current_turn_idx]
        app_module.set_player_ai_enabled(room_id, player.uid, True)
        room["decision_seq"] = 17
        room["ai_control_seq"] = 9
        room["ai_busy"] = True
        before_hand = list(player.hand)
        emitted = []

        with patch.dict(os.environ, {"AI_PROVIDER": "ollama"}), patch.object(
            VisibleMahjongAI,
            "choose_discard",
            return_value=0,
        ) as choose_discard, patch.object(
            app_module.socketio,
            "emit",
            side_effect=lambda event, payload=None, **kwargs: emitted.append((event, payload, kwargs)),
        ), patch.object(app_module, "schedule_decision_timeout", return_value=None):
            app_module.timeout_discard_current_player(room_id, 17)

        choose_discard.assert_called_once_with(game, player.idx, use_api=False)
        self.assertEqual(room["ai_control_seq"], 10)
        self.assertFalse(room["ai_busy"])
        self.assertEqual(player.discards[-1], before_hand[0])
        timeout_event = next(payload for event, payload, _kwargs in emitted if event == "turn_timeout")
        self.assertEqual(timeout_event["reason"], "PYTHON_FALLBACK")

    def test_stale_ollama_result_cannot_discard_after_takeover(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        game = room["game"]
        for idx in range(4):
            uid = f"u{idx}"
            game.add_player(uid, f"P{idx}")
            room["players"].append(
                {
                    "uid": uid,
                    "name": f"P{idx}",
                    "connected": True,
                    "ai_enabled": False,
                    "auto_play_enabled": False,
                }
            )
        room["state"] = "GAME"
        game.start_game()
        player = game.players[game.current_turn_idx]
        app_module.set_player_ai_enabled(room_id, player.uid, True)
        room["ai_control_seq"] = 21
        before_hand = list(player.hand)

        def stale_choice(_game, _player_idx):
            room["ai_control_seq"] += 1
            return 0

        with patch.object(game, "check_self_actions", return_value=[]), patch.object(
            VisibleMahjongAI,
            "choose_discard",
            side_effect=stale_choice,
        ):
            progressed = app_module.perform_ai_step(room_id, 21)

        self.assertFalse(progressed)
        self.assertEqual(player.hand, before_hand)

    def test_claim_timeout_passes_without_enabling_ai(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        game = room["game"]
        for idx in range(4):
            uid = f"u{idx}"
            game.add_player(uid, f"P{idx}")
            room["players"].append(
                {
                    "uid": uid,
                    "name": f"P{idx}",
                    "connected": True,
                    "ai_enabled": False,
                }
            )

        room["state"] = "GAME"
        game.start_game()
        game.players[0].hand = ["5m"]
        game.players[1].hand = ["3m", "4m"]
        game.players[2].hand = ["5m", "5m"]
        game.players[3].hand = ["1p"]
        game.discard_tile("u0", 0)
        room["decision_seq"] = 9

        old_timeout = app_module.DECISION_TIMEOUT_SECONDS
        old_schedule = app_module.schedule_decision_timeout
        try:
            app_module.DECISION_TIMEOUT_SECONDS = 0
            app_module.schedule_decision_timeout = lambda _room_id: None
            app_module.run_decision_timeout(room_id, 9)
        finally:
            app_module.DECISION_TIMEOUT_SECONDS = old_timeout
            app_module.schedule_decision_timeout = old_schedule

        self.assertEqual(game.state, STATE_PLAYER_TURN)
        self.assertTrue(all(not player["ai_enabled"] for player in room["players"]))

    def test_discard_event_echoes_client_action_id(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        game = room["game"]
        for idx in range(4):
            game.add_player(f"u{idx}", f"P{idx}")
        game.start_game()
        game.discard_tile("u0", 0)

        emitted = []
        old_emit = app_module.socketio.emit
        try:
            app_module.socketio.emit = lambda event, payload, **kwargs: emitted.append((event, payload, kwargs))
            app_module.emit_discard_event(room_id, "u0", 0, "client-action-1")
        finally:
            app_module.socketio.emit = old_emit

        self.assertEqual(emitted[0][0], "game_event")
        self.assertEqual(emitted[0][1]["client_action_id"], "client-action-1")
        self.assertEqual(emitted[0][1]["state_seq"], room["state_seq"] + 1)
        self.assertEqual(emitted[0][1]["hand_count_after"], len(game.players[0].hand))
        self.assertEqual(emitted[0][1]["discard_count_after"], len(game.players[0].discards))

    def test_offline_grace_period_is_tracked_per_player(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        for idx in range(4):
            uid = f"u{idx}"
            room["game"].add_player(uid, f"P{idx}")
            room["players"].append(
                {
                    "uid": uid,
                    "name": f"P{idx}",
                    "connected": True,
                    "ai_enabled": False,
                    "connection_seq": 0,
                    "offline_deadline": None,
                }
            )

        old_start_task = app_module.socketio.start_background_task
        scheduled = []
        try:
            app_module.socketio.start_background_task = lambda target, *args: scheduled.append((target, args))
            for idx in range(4):
                app_module.schedule_player_offline(room_id, f"u{idx}")
        finally:
            app_module.socketio.start_background_task = old_start_task

        self.assertEqual(len(scheduled), 4)
        self.assertEqual([item[1][1] for item in scheduled], ["u0", "u1", "u2", "u3"])
        self.assertTrue(all(player["connected"] for player in room["players"]))
        self.assertTrue(all(player["offline_deadline"] is not None for player in room["players"]))

        # Reconnecting one player invalidates only that player's pending timer.
        app_module.set_player_connected(room_id, "u0", True)
        old_timeout = app_module.OFFLINE_TIMEOUT_SECONDS
        try:
            app_module.OFFLINE_TIMEOUT_SECONDS = 0
            for _, args in scheduled:
                app_module.run_player_offline_timeout(*args)
        finally:
            app_module.OFFLINE_TIMEOUT_SECONDS = old_timeout

        self.assertTrue(room["players"][0]["connected"])
        self.assertTrue(all(not player["connected"] for player in room["players"][1:]))

    def test_offline_timeout_enables_separate_auto_play_and_auto_readies(self):
        room_id = app_module.create_room_state("custom", None, False, 10)
        room = app_module.rooms[room_id]
        game = room["game"]
        for idx in range(4):
            uid = f"u{idx}"
            game.add_player(uid, f"P{idx}")
            room["players"].append(
                {
                    "uid": uid,
                    "name": f"P{idx}",
                    "connected": True,
                    "ai_enabled": False,
                    "auto_play_enabled": False,
                    "connection_seq": 0,
                    "offline_deadline": None,
                }
            )

        game.start_game()
        room["state"] = app_module.ROOM_HAND_ENDED
        room["players"][0]["connection_seq"] = 4
        old_timeout = app_module.OFFLINE_TIMEOUT_SECONDS
        old_start_task = app_module.socketio.start_background_task
        try:
            app_module.OFFLINE_TIMEOUT_SECONDS = 0
            app_module.socketio.start_background_task = lambda target, *args: None
            app_module.run_player_offline_timeout(room_id, "u0", 4)
        finally:
            app_module.OFFLINE_TIMEOUT_SECONDS = old_timeout
            app_module.socketio.start_background_task = old_start_task

        player = room["players"][0]
        self.assertFalse(player["connected"])
        self.assertFalse(player["ai_enabled"])
        self.assertTrue(player["auto_play_enabled"])
        self.assertTrue(app_module.is_ai_controlled(room_id, "u0"))
        self.assertIn("u0", room["next_round_votes"])
        app_module.set_player_connected(room_id, "u0", True)
        self.assertFalse(player["auto_play_enabled"])
        self.assertFalse(app_module.is_ai_controlled(room_id, "u0"))
        self.assertNotIn("u0", room["next_round_votes"])

    def test_unranked_room_can_start_a_fresh_match_after_four_rematch_votes(self):
        clients = [app_module.socketio.test_client(app_module.app) for _ in range(4)]
        for idx, client in enumerate(clients):
            self.auth_client(client, f"Rematch{idx}")

        clients[0].emit("create_room", {})
        created = next(event for event in clients[0].get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]
        for idx in range(1, 4):
            clients[idx].emit("join_room", {"room_id": room_id})

        room = app_module.rooms[room_id]
        game = room["game"]
        old_match_id = game.match.match_id
        game.match.finish({player.uid: player.idx for player in game.players})
        room["state"] = app_module.ROOM_MATCH_ENDED

        for client in clients:
            client.emit("request_rematch", {})

        self.assertEqual(room["state"], "GAME")
        self.assertNotEqual(game.match.match_id, old_match_id)
        self.assertEqual(game.match.hand_number, 1)
        self.assertEqual(room["rematch_votes"], set())
        self.assertTrue(all(not player["ai_enabled"] for player in room["players"]))
        self.assertTrue(all(not player["auto_play_enabled"] for player in room["players"]))

        for client in clients:
            client.disconnect()

    def test_ranked_room_rejects_same_room_rematch(self):
        client = app_module.socketio.test_client(app_module.app)
        auth = self.auth_client(client, "NoRematch")
        room_id = app_module.create_room_state("ranked", None, True, 10)
        room = app_module.rooms[room_id]
        room["game"].add_player(auth["account"]["id"], auth["account"]["username"])
        room["players"].append(
            {
                "uid": auth["account"]["id"],
                "name": auth["account"]["username"],
                "connected": True,
                "ai_enabled": False,
                "auto_play_enabled": False,
            }
        )
        room["state"] = app_module.ROOM_MATCH_ENDED
        app_module.rooms_by_uid[auth["account"]["id"]] = room_id

        client.emit("request_rematch", {})
        received = client.get_received()

        self.assertTrue(any(event["name"] == "server_error" for event in received))
        self.assertEqual(room["rematch_votes"], set())
        client.disconnect()

    def test_leaver_is_fourth_and_ranked_room_dissolves_for_everyone(self):
        old_manager = app_module.account_manager
        clients = []
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = AccountManager(os.path.join(temp_dir, "accounts.db"))
                app_module.account_manager = manager
                clients = [app_module.socketio.test_client(app_module.app) for _ in range(4)]
                auth = [self.auth_client(client, f"Forfeit{idx}") for idx, client in enumerate(clients)]
                for client in clients:
                    client.get_received()
                    client.emit("join_ranked", {})

                room_id = next(
                    room_id
                    for room_id, room in app_module.rooms.items()
                    if room["mode"] == "ranked"
                )
                leaver_uid = auth[0]["account"]["id"]
                room = app_module.rooms[room_id]
                room["game"].match.cumulative_scores = {
                    item["account"]["id"]: (12 if index == 0 else -4)
                    for index, item in enumerate(auth)
                }
                for client in clients:
                    client.get_received()

                clients[0].emit("leave_room", {})
                received_by_client = [client.get_received() for client in clients]

                self.assertNotIn(room_id, app_module.rooms)
                self.assertTrue(all(uid not in app_module.rooms_by_uid for uid in [item["account"]["id"] for item in auth]))
                self.assertTrue(
                    all(any(event["name"] == "room_dissolved" for event in events) for events in received_by_client)
                )

                leaver_history = manager.get_recent_history(leaver_uid)
                self.assertEqual(leaver_history[0]["final_rank"], 4)
                self.assertEqual(leaver_history[0]["rank_delta"], -30)
                self.assertEqual(leaver_history[0]["coin_delta"], -10)
                self.assertEqual(manager.get_profile(leaver_uid)["coins"], 0)
        finally:
            app_module.account_manager = old_manager
            for client in clients:
                if client.is_connected():
                    client.disconnect()

    def test_resume_reconnects_to_same_lobby_slot(self):
        client = app_module.socketio.test_client(app_module.app)
        auth = self.auth_client(client, "Reconnect")
        token = auth["token"]
        account_id = auth["account"]["id"]

        client.emit("create_room", {})
        created = next(event for event in client.get_received() if event["name"] == "room_created")
        room_id = created["args"][0]["room_id"]
        client.disconnect()

        player = app_module.rooms[room_id]["players"][0]
        self.assertTrue(player["connected"])
        self.assertIsNotNone(player["offline_deadline"])

        new_client = app_module.socketio.test_client(app_module.app)
        new_client.emit("resume_session", {"token": token})
        received = new_client.get_received()

        self.assertTrue(any(event["name"] == "auth_success" for event in received))
        self.assertTrue(any(event["name"] == "rejoin_success" for event in received))
        self.assertEqual(app_module.rooms_by_uid[account_id], room_id)
        self.assertTrue(app_module.rooms[room_id]["players"][0]["connected"])

        new_client.disconnect()


if __name__ == "__main__":
    unittest.main()
