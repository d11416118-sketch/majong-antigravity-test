import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.rules import calculate_fan, can_hu, get_ting_tiles


class TestTaiwanHuAndScoring(unittest.TestCase):
    def test_basic_16_tile_hu(self):
        hand = [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "5m", "5m", "5m",
            "9m",
        ]
        self.assertTrue(can_hu(hand, "9m"))

    def test_open_meld_hu_uses_meld_count(self):
        hand = [
            "2m", "3m", "4m",
            "5m", "6m", "7m",
            "2p", "3p", "4p",
            "5z", "5z", "5z",
            "8s",
        ]
        self.assertTrue(can_hu(hand, "8s", meld_count=1))

    def test_ting_tiles(self):
        hand = [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "5m", "5m", "5m",
            "9m",
        ]
        self.assertIn("9m", get_ting_tiles(hand, []))

    def test_all_triplets_mixed_color_scoring(self):
        hand = [
            "1m", "1m", "1m",
            "3m", "3m", "3m",
            "5m", "5m", "5m",
            "7m", "7m", "7m",
            "9m", "9m", "9m",
            "1z",
        ]
        result = calculate_fan(hand, [], "1z", False, 0, 0, [])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("碰碰胡", names)
        self.assertIn("混一色", names)
        self.assertIn("門清", names)
        self.assertIn("五暗刻", names)
        self.assertIn("獨聽", names)
        self.assertEqual(result["total"], 19)

    def test_pure_color_scoring(self):
        hand = [
            "1m", "1m", "1m",
            "2m", "2m", "2m",
            "3m", "3m", "3m",
            "4m", "4m", "4m",
            "5m", "5m", "5m",
            "6m",
        ]
        result = calculate_fan(hand, [], "6m", False, 0, 0, [])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("清一色", names)
        self.assertIn("碰碰胡", names)
        self.assertIn("五暗刻", names)
        self.assertEqual(result["total"], 22)

    def test_dragon_pung_scoring(self):
        hand = [
            "1m", "2m", "3m",
            "4m", "5m", "6m",
            "7m", "8m", "9m",
            "1s", "2s", "3s",
            "5z", "5z", "5z",
            "9s",
        ]
        result = calculate_fan(hand, [], "9s", False, 0, 0, [])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("三元牌 中", names)
        self.assertIn("獨聽", names)
        self.assertEqual(result["total"], 4)

    def test_dealer_zimo_flowers(self):
        hand = [
            "1m", "2m", "3m",
            "2m", "3m", "4m",
            "5p", "6p", "7p",
            "2s", "3s", "4s",
            "7z", "7z", "7z",
            "8s", "8s",
        ]
        result = calculate_fan(hand, [], "8s", True, 0, 0, ["1f", "5f"], is_dealer=True, lian_zhuang=1)
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("莊家", names)
        self.assertIn("門清自摸", names)
        self.assertNotIn("自摸", names)
        self.assertNotIn("門清", names)
        self.assertIn("正花 春", names)
        self.assertIn("正花 梅", names)
        self.assertIn("連莊 x1", names)
        self.assertEqual(result["base"], 1)
        self.assertEqual(result["fan_total"], result["total"] - 1)

    def test_pinfu_scoring(self):
        hand = [
            "1m", "2m", "3m",
            "4m", "5m", "6m",
            "7m", "8m", "9m",
            "1p", "2p", "3p",
            "4p", "5p",
            "7s", "7s",
        ]
        result = calculate_fan(hand, [], "3p", False, 0, 0, [])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("平胡", names)
        self.assertNotIn("獨聽", names)
        self.assertEqual(result["total"], 4)

    def test_small_dragon_scoring(self):
        hand = [
            "1m", "2m", "3m",
            "2m", "3m", "4m",
            "5p", "6p", "7p",
            "5z", "5z", "5z",
            "6z", "6z", "6z",
            "7z",
        ]
        result = calculate_fan(hand, [], "7z", False, 0, 0, [])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("小三元", names)
        self.assertNotIn("三元牌 中", names)
        self.assertIn("獨聽", names)
        self.assertEqual(result["total"], 7)

    def test_kang_and_flower_gang_scoring(self):
        hand = [
            "1m", "2m", "3m",
            "4p", "5p", "6p",
            "7z",
        ]
        melds = [
            {"type": "KANG", "tile": "9m", "tiles": ["9m"] * 4},
            {"type": "ANKANG", "tile": "9p", "tiles": ["BACK", "9p", "9p", "BACK"], "concealed": True},
            {"type": "BUKANG", "tile": "9s", "tiles": ["9s"] * 4},
        ]
        result = calculate_fan(hand, melds, "7z", False, 0, 0, ["1f", "2f", "3f", "4f"])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("三槓子", names)
        self.assertIn("花槓 春夏秋冬", names)
        self.assertNotIn("正花 春", names)

    def test_only_matching_seat_flowers_score(self):
        hand = [
            "1m", "2m", "3m",
            "2m", "3m", "4m",
            "5p", "6p", "7p",
            "2s", "3s", "4s",
            "7z", "7z", "7z",
            "8s", "8s",
        ]
        west = calculate_fan(hand, [], "8s", False, 2, 0, ["3f", "8f", "1f"])
        values = {item["name"]: item["value"] for item in west["breakdown"]}

        self.assertEqual(values["正花 秋"], 1)
        self.assertEqual(values["正花 菊"], 1)
        self.assertNotIn("正花 春", values)

    def test_declared_ting_and_di_ting_do_not_stack_with_menqing(self):
        hand = [
            "1m", "2m", "3m",
            "2m", "3m", "4m",
            "5p", "6p", "7p",
            "2s", "3s", "4s",
            "7z", "7z", "7z",
            "8s", "8s",
        ]
        declared = calculate_fan(hand, [], "8s", False, 0, 0, [], is_declared_ting=True)
        declared_names = [item["name"] for item in declared["breakdown"]]
        self.assertIn("宣告聽牌", declared_names)

        di_ting = calculate_fan(
            hand,
            [],
            "8s",
            False,
            0,
            0,
            [],
            is_declared_ting=True,
            is_di_ting=True,
        )
        di_values = {item["name"]: item["value"] for item in di_ting["breakdown"]}
        self.assertEqual(di_values["地聽"], 4)
        self.assertNotIn("宣告聽牌", di_values)
        self.assertNotIn("門清", di_values)

    def test_big_winds_and_all_honors_scoring(self):
        hand = [
            "1z", "1z", "1z",
            "2z", "2z", "2z",
            "3z", "3z", "3z",
            "4z", "4z", "4z",
            "5z", "5z", "5z",
            "6z",
        ]
        result = calculate_fan(hand, [], "6z", False, 0, 0, [])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("字一色", names)
        self.assertEqual(next(item["value"] for item in result["breakdown"] if item["name"] == "字一色"), 8)
        self.assertIn("大四喜", names)
        self.assertNotIn("碰碰胡", names)

    def test_small_four_winds_stacks_matching_seat_and_round_winds(self):
        hand = [
            "1z", "1z", "1z",
            "2z", "2z", "2z",
            "3z", "3z", "3z",
            "4z", "4z",
            "1m", "2m", "3m",
            "4m", "5m", "6m",
        ]
        result = calculate_fan(hand, [], "6m", False, 0, 1, [])
        names = [item["name"] for item in result["breakdown"]]

        self.assertIn("小四喜", names)
        self.assertIn("門風 東", names)
        self.assertIn("圈風 南", names)

    def test_standard_special_fan_values(self):
        hand = [
            "1m", "2m", "3m",
            "2m", "3m", "4m",
            "5p", "6p", "7p",
            "2s", "3s", "4s",
            "7z", "7z", "7z",
            "8s", "8s",
        ]
        special_fans = {
            "qiang_gang": 1,
            "gang_shang": 1,
            "haidi": 1,
            "hedi": 1,
            "tian_hu": 24,
            "di_hu": 16,
            "ren_hu": 8,
        }
        result = calculate_fan(
            hand,
            [],
            "8s",
            False,
            0,
            0,
            [],
            is_qiang_gang=True,
            is_gang_shang=True,
            is_haidi=True,
            is_hedi=True,
            special_fans=special_fans,
        )
        values = {item["name"]: item["value"] for item in result["breakdown"]}
        self.assertEqual(values["搶槓"], 1)
        self.assertEqual(values["槓上開花"], 1)
        self.assertEqual(values["海底撈月"], 1)
        self.assertEqual(values["河底撈魚"], 1)

        for flag, name, value in (
            ("is_tian_hu", "天胡", 24),
            ("is_di_hu", "地胡", 16),
            ("is_ren_hu", "人胡", 8),
        ):
            opening = calculate_fan(
                hand,
                [],
                "8s",
                flag != "is_ren_hu",
                0,
                0,
                [],
                special_fans=special_fans,
                **{flag: True},
            )
            opening_values = {item["name"]: item["value"] for item in opening["breakdown"]}
            self.assertEqual(opening_values[name], value)

    def test_full_and_half_ask_do_not_repeat_forced_single_wait(self):
        melds = [
            {"type": "CHI", "tile": f"{rank}m", "tiles": [f"{rank}m", f"{rank + 1}m", f"{rank + 2}m"]}
            for rank in range(1, 6)
        ]
        full = calculate_fan(["9s"], melds, "9s", False, 0, 0, [])
        full_names = [item["name"] for item in full["breakdown"]]
        self.assertIn("全求人", full_names)
        self.assertNotIn("獨聽", full_names)

        half = calculate_fan(["9s", "9s"], melds, "9s", True, 0, 0, [])
        half_names = [item["name"] for item in half["breakdown"]]
        self.assertIn("半求人", half_names)
        self.assertNotIn("獨聽", half_names)


if __name__ == "__main__":
    unittest.main()
