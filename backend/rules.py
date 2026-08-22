from collections import Counter
from typing import Dict, List, Optional


SUITS = ("m", "p", "s")
HONORS = ("1z", "2z", "3z", "4z", "5z", "6z", "7z")
FLOWERS = ("1f", "2f", "3f", "4f", "5f", "6f", "7f", "8f")

DRAGON_NAMES = {"5z": "中", "6z": "發", "7z": "白"}
WIND_NAMES = ("東", "南", "西", "北")
DRAGON_TILES = tuple(DRAGON_NAMES.keys())
WIND_TILES = ("1z", "2z", "3z", "4z")
KANG_TYPES = {"KANG", "ANKANG", "BUKANG"}

ACTION_PRIORITY = {
    "PASS": 0,
    "CHI": 1,
    "PON": 2,
    "KANG": 2,
    "HU": 3,
}


def tile_sort_key(tile: str) -> tuple[int, int]:
    if not tile:
        return (99, 99)
    suit = tile[-1]
    try:
        rank = int(tile[:-1])
    except ValueError:
        rank = 99
    return ({"m": 0, "p": 1, "s": 2, "z": 3, "f": 4}.get(suit, 99), rank)


def all_play_tiles() -> List[str]:
    tiles: List[str] = []
    for suit in SUITS:
        tiles.extend(f"{rank}{suit}" for rank in range(1, 10))
    tiles.extend(HONORS)
    return tiles


def build_wall() -> List[str]:
    tiles: List[str] = []
    for tile in all_play_tiles():
        tiles.extend([tile] * 4)
    tiles.extend(FLOWERS)
    return tiles


def is_flower(tile: str) -> bool:
    return tile.endswith("f")


def is_suited(tile: str) -> bool:
    return tile[-1] in SUITS


def can_chi(hand: List[str], tile: str) -> List[List[str]]:
    if not is_suited(tile):
        return []

    rank = int(tile[:-1])
    suit = tile[-1]
    choices: List[List[str]] = []

    for left, right in ((rank - 2, rank - 1), (rank - 1, rank + 1), (rank + 1, rank + 2)):
        if left < 1 or right > 9:
            continue
        pair = [f"{left}{suit}", f"{right}{suit}"]
        test_hand = list(hand)
        if pair[0] in test_hand:
            test_hand.remove(pair[0])
            if pair[1] in test_hand:
                choices.append(pair)

    return choices


def can_pon(hand: List[str], tile: str) -> bool:
    return hand.count(tile) >= 2


def can_kang(hand: List[str], tile: str) -> bool:
    return hand.count(tile) >= 3


def can_hu(hand: List[str], tile: Optional[str] = None, meld_count: int = 0) -> bool:
    tiles = list(hand)
    if tile is not None:
        tiles.append(tile)

    if any(is_flower(t) for t in tiles):
        return False

    expected_count = (5 - meld_count) * 3 + 2
    if expected_count < 2 or len(tiles) != expected_count:
        return False

    tiles.sort(key=tile_sort_key)
    return _check_standard_hu(tiles)


def _check_standard_hu(tiles: List[str]) -> bool:
    counts = Counter(tiles)

    for pair_tile, count in list(counts.items()):
        if count < 2:
            continue

        counts[pair_tile] -= 2
        if _can_form_sets(counts):
            counts[pair_tile] += 2
            return True
        counts[pair_tile] += 2

    return False


def _can_form_sets(counts: Counter) -> bool:
    first = next((tile for tile, count in sorted(counts.items(), key=lambda item: tile_sort_key(item[0])) if count), None)
    if first is None:
        return True

    if counts[first] >= 3:
        counts[first] -= 3
        if _can_form_sets(counts):
            counts[first] += 3
            return True
        counts[first] += 3

    if is_suited(first):
        rank = int(first[:-1])
        suit = first[-1]
        seq = [first, f"{rank + 1}{suit}", f"{rank + 2}{suit}"]
        if rank <= 7 and all(counts[tile] > 0 for tile in seq):
            for tile in seq:
                counts[tile] -= 1
            if _can_form_sets(counts):
                for tile in seq:
                    counts[tile] += 1
                return True
            for tile in seq:
                counts[tile] += 1

    return False


def resolve_actions(actions: List[Dict], from_idx: Optional[int] = None) -> Optional[Dict]:
    valid_actions = [action for action in actions if action.get("type") != "PASS"]
    if not valid_actions:
        return None

    first_hu = next(
        (action for action in valid_actions if action.get("type") == "HU"),
        None,
    )
    if first_hu:
        return first_hu

    return max(
        valid_actions,
        key=lambda action: (
            ACTION_PRIORITY.get(action.get("type", "PASS"), 0),
            -_seat_distance(from_idx, action.get("player_idx")),
        ),
    )


def _seat_distance(from_idx: Optional[int], player_idx: Optional[int]) -> int:
    if from_idx is None or player_idx is None:
        return 0
    distance = (player_idx - from_idx) % 4
    return distance or 4


def get_ting_tiles(hand: List[str], melds: List[Dict]) -> List[str]:
    meld_count = len(melds)
    return [tile for tile in all_play_tiles() if can_hu(hand, tile, meld_count)]


def calculate_fan(
    hand: List[str],
    melds: List[Dict],
    winning_tile: Optional[str],
    is_zimo: bool,
    seat_wind: int,
    round_wind: int,
    flowers: Optional[List[str]] = None,
    is_dealer: bool = False,
    lian_zhuang: int = 0,
    is_qiang_gang: bool = False,
    is_gang_shang: bool = False,
    is_haidi: bool = False,
    is_hedi: bool = False,
    is_tian_hu: bool = False,
    is_di_hu: bool = False,
    is_ren_hu: bool = False,
    is_declared_ting: bool = False,
    is_di_ting: bool = False,
    special_fans: Optional[Dict[str, int]] = None,
    base_score: int = 1,
) -> Dict:
    flowers = flowers or []
    concealed_tiles = _winning_concealed_tiles(hand, melds, winning_tile)
    all_counts = _all_tile_counts(concealed_tiles, melds)
    decompositions = _standard_decompositions(concealed_tiles)
    exposed_melds = [meld for meld in melds if not meld.get("concealed")]
    is_menqing = not exposed_melds
    is_du_ting = _is_single_wait(hand, melds, winning_tile)
    all_melds_exposed = len(melds) == 5 and all(not meld.get("concealed") for meld in melds)
    is_full_ask = not is_zimo and all_melds_exposed and len(concealed_tiles) == 2
    is_half_ask = is_zimo and all_melds_exposed and len(concealed_tiles) == 2

    special_fans = special_fans or {}
    total = max(0, int(base_score))
    breakdown = [{"name": "底", "value": total}] if total else []

    if is_dealer:
        total += 1
        breakdown.append({"name": "莊家", "value": 1})

    if lian_zhuang > 0:
        value = lian_zhuang * 2
        total += value
        breakdown.append({"name": f"連莊 x{lian_zhuang}", "value": value})

    opening_self_draw = is_tian_hu or is_di_hu
    is_menqing_zimo = is_zimo and is_menqing and not opening_self_draw and not is_di_ting
    if is_menqing_zimo:
        total += 3
        breakdown.append({"name": "門清自摸", "value": 3})
    elif is_zimo and not opening_self_draw:
        total += 1
        breakdown.append({"name": "自摸", "value": 1})

    if is_qiang_gang:
        value = int(special_fans.get("qiang_gang", 1))
        total += value
        breakdown.append({"name": "搶槓", "value": value})

    if is_gang_shang and not is_tian_hu:
        value = int(special_fans.get("gang_shang", 1))
        total += value
        breakdown.append({"name": "槓上開花", "value": value})

    if is_haidi:
        value = int(special_fans.get("haidi", 1))
        total += value
        breakdown.append({"name": "海底撈月", "value": value})

    if is_hedi:
        value = int(special_fans.get("hedi", 1))
        total += value
        breakdown.append({"name": "河底撈魚", "value": value})

    opening_fans = (
        ("天胡", is_tian_hu, int(special_fans.get("tian_hu", 24))),
        ("地胡", is_di_hu, int(special_fans.get("di_hu", 16))),
        ("人胡", is_ren_hu, int(special_fans.get("ren_hu", 8))),
    )
    for name, matched, value in opening_fans:
        if matched:
            total += value
            breakdown.append({"name": name, "value": value})

    if is_di_ting:
        value = int(special_fans.get("di_ting", 4))
        total += value
        breakdown.append({"name": "地聽", "value": value})
    elif is_declared_ting:
        value = int(special_fans.get("declared_ting", 1))
        total += value
        breakdown.append({"name": "宣告聽牌", "value": value})

    for name, value in _flower_fans(flowers, seat_wind):
        total += value
        breakdown.append({"name": name, "value": value})

    if is_menqing and not is_menqing_zimo and not is_di_ting and not (is_tian_hu or is_di_hu or is_ren_hu):
        total += 1
        breakdown.append({"name": "門清", "value": 1})

    if is_du_ting and not (is_full_ask or is_half_ask):
        total += 1
        breakdown.append({"name": "獨聽", "value": 1})

    is_all_honors = _is_all_honors(all_counts)
    if is_all_honors:
        total += 8
        breakdown.append({"name": "字一色", "value": 8})
    else:
        color_fan = _color_fan(concealed_tiles, melds)
        if color_fan:
            total += color_fan["value"]
            breakdown.append(color_fan)

    if not is_all_honors and _has_all_triplets(decompositions, concealed_tiles, melds):
        total += 4
        breakdown.append({"name": "碰碰胡", "value": 4})
    elif _has_pinfu(
        decompositions,
        melds,
        flowers,
        seat_wind,
        round_wind,
        is_zimo,
        is_du_ting,
    ):
        total += 2
        breakdown.append({"name": "平胡", "value": 2})

    concealed_triplet_fan = _concealed_triplet_fan(
        _max_concealed_triplets(decompositions, melds, hand, winning_tile, is_zimo)
    )
    if concealed_triplet_fan:
        total += concealed_triplet_fan["value"]
        breakdown.append(concealed_triplet_fan)

    kang_fan = _kang_fan(melds)
    if kang_fan:
        total += kang_fan["value"]
        breakdown.append(kang_fan)

    if is_full_ask:
        total += 2
        breakdown.append({"name": "全求人", "value": 2})
    elif is_half_ask:
        total += 1
        breakdown.append({"name": "半求人", "value": 1})

    dragon_fan = _dragon_fan(all_counts)
    if dragon_fan:
        total += dragon_fan["value"]
        breakdown.append(dragon_fan)
    else:
        for tile, name in DRAGON_NAMES.items():
            if all_counts.get(tile, 0) >= 3:
                total += 1
                breakdown.append({"name": f"三元牌 {name}", "value": 1})

    wind_fan = _wind_fan(all_counts)
    if wind_fan:
        total += wind_fan["value"]
        breakdown.append(wind_fan)
    if not wind_fan or wind_fan["name"] != "大四喜":
        seat_tile = f"{seat_wind + 1}z"
        if all_counts.get(seat_tile, 0) >= 3:
            total += 1
            breakdown.append({"name": f"門風 {WIND_NAMES[seat_wind]}", "value": 1})

        round_tile = f"{round_wind + 1}z"
        if all_counts.get(round_tile, 0) >= 3:
            total += 1
            breakdown.append({"name": f"圈風 {WIND_NAMES[round_wind]}", "value": 1})

    base_value = max(0, int(base_score))
    return {
        "base": base_value,
        "fan_total": max(0, total - base_value),
        "total": total,
        "breakdown": breakdown,
    }


def _winning_concealed_tiles(hand: List[str], melds: List[Dict], winning_tile: Optional[str]) -> List[str]:
    tiles = list(hand)
    expected_count = (5 - len(melds)) * 3 + 2
    if winning_tile is not None and len(tiles) < expected_count:
        tiles.append(winning_tile)
    return tiles


def _is_single_wait(hand: List[str], melds: List[Dict], winning_tile: Optional[str]) -> bool:
    if not winning_tile:
        return False
    before_win = list(hand)
    expected_before_win = (5 - len(melds)) * 3 + 1
    if len(before_win) == expected_before_win + 1:
        if winning_tile not in before_win:
            return False
        before_win.remove(winning_tile)
    if len(before_win) != expected_before_win:
        return False
    return get_ting_tiles(before_win, melds) == [winning_tile]


def _all_tile_counts(concealed_tiles: List[str], melds: List[Dict]) -> Counter:
    counts = Counter(tile for tile in concealed_tiles if tile and not is_flower(tile))
    for meld in melds:
        meld_type = meld.get("type")
        tile = meld.get("tile")
        if meld_type == "PON" and tile:
            counts[tile] += 3
        elif meld_type in KANG_TYPES and tile:
            counts[tile] += 4
        elif meld_type == "CHI":
            counts.update(tile for tile in meld.get("tiles", []) if tile and tile != "BACK" and not is_flower(tile))
        else:
            counts.update(tile for tile in meld.get("tiles", [tile]) if tile and tile != "BACK" and not is_flower(tile))
    return counts


def _standard_decompositions(tiles: List[str]) -> List[Dict]:
    if len(tiles) % 3 != 2 or any(is_flower(tile) for tile in tiles):
        return []

    results: List[Dict] = []
    counts = Counter(tiles)

    def search(current: Counter, sets: List[Dict], pair: Optional[str]) -> None:
        if len(results) >= 96:
            return

        first = next((tile for tile, count in sorted(current.items(), key=lambda item: tile_sort_key(item[0])) if count), None)
        if first is None:
            if pair is not None:
                results.append({"sets": list(sets), "pair": pair})
            return

        if pair is None and current[first] >= 2:
            current[first] -= 2
            search(current, sets, first)
            current[first] += 2

        if current[first] >= 3:
            current[first] -= 3
            sets.append({"kind": "triplet", "tile": first, "tiles": [first, first, first]})
            search(current, sets, pair)
            sets.pop()
            current[first] += 3

        if is_suited(first):
            rank = int(first[:-1])
            suit = first[-1]
            sequence = [first, f"{rank + 1}{suit}", f"{rank + 2}{suit}"]
            if rank <= 7 and all(current[tile] > 0 for tile in sequence):
                for tile in sequence:
                    current[tile] -= 1
                sets.append({"kind": "sequence", "tiles": sequence})
                search(current, sets, pair)
                sets.pop()
                for tile in sequence:
                    current[tile] += 1

    search(counts, [], None)
    return results


def _has_all_triplets(decompositions: List[Dict], concealed_tiles: List[str], melds: List[Dict]) -> bool:
    if any(meld.get("type") == "CHI" for meld in melds):
        return False
    if decompositions:
        return any(all(item["kind"] == "triplet" for item in decomp["sets"]) for decomp in decompositions)
    return _is_all_triplets(concealed_tiles, melds)


def _has_pinfu(
    decompositions: List[Dict],
    melds: List[Dict],
    flowers: List[str],
    seat_wind: int,
    round_wind: int,
    is_zimo: bool,
    is_du_ting: bool,
) -> bool:
    if is_zimo or is_du_ting or flowers or any(meld.get("type") != "CHI" for meld in melds):
        return False

    value_pairs = set(DRAGON_TILES)
    value_pairs.add(f"{seat_wind + 1}z")
    value_pairs.add(f"{round_wind + 1}z")
    for decomp in decompositions:
        if decomp["pair"].endswith("z") or decomp["pair"] in value_pairs:
            continue
        if all(item["kind"] == "sequence" for item in decomp["sets"]):
            return True
    return False


def _max_concealed_triplets(
    decompositions: List[Dict],
    melds: List[Dict],
    hand: List[str],
    winning_tile: Optional[str],
    is_zimo: bool,
) -> int:
    concealed_kangs = sum(1 for meld in melds if meld.get("type") == "ANKANG")
    if not decompositions:
        return concealed_kangs

    original_counts = Counter(hand)
    max_count = 0
    for decomp in decompositions:
        count = concealed_kangs
        for item in decomp["sets"]:
            if item["kind"] != "triplet":
                continue
            tile = item["tile"]
            if not is_zimo and winning_tile == tile and original_counts[tile] < 3:
                continue
            count += 1
        max_count = max(max_count, count)
    return max_count


def _concealed_triplet_fan(count: int) -> Optional[Dict]:
    if count >= 5:
        return {"name": "五暗刻", "value": 8}
    if count == 4:
        return {"name": "四暗刻", "value": 5}
    if count == 3:
        return {"name": "三暗刻", "value": 2}
    return None


def _kang_fan(melds: List[Dict]) -> Optional[Dict]:
    count = sum(1 for meld in melds if meld.get("type") in KANG_TYPES)
    if count >= 5:
        return {"name": "五槓子", "value": 16}
    if count == 4:
        return {"name": "四槓子", "value": 8}
    if count == 3:
        return {"name": "三槓子", "value": 2}
    return None


def _dragon_fan(counts: Counter) -> Optional[Dict]:
    triplets = [tile for tile in DRAGON_TILES if counts.get(tile, 0) >= 3]
    if len(triplets) == 3:
        return {"name": "大三元", "value": 8}
    if len(triplets) == 2 and any(counts.get(tile, 0) >= 2 for tile in DRAGON_TILES if tile not in triplets):
        return {"name": "小三元", "value": 4}
    return None


def _wind_fan(counts: Counter) -> Optional[Dict]:
    triplets = [tile for tile in WIND_TILES if counts.get(tile, 0) >= 3]
    if len(triplets) == 4:
        return {"name": "大四喜", "value": 16}
    if len(triplets) == 3 and any(counts.get(tile, 0) >= 2 for tile in WIND_TILES if tile not in triplets):
        return {"name": "小四喜", "value": 8}
    return None


def _is_all_honors(counts: Counter) -> bool:
    return bool(counts) and all(tile.endswith("z") for tile in counts)


def _flower_fans(flowers: List[str], seat_wind: int) -> List[tuple[str, int]]:
    flower_set = set(flowers)
    result: List[tuple[str, int]] = []
    groups = (
        ({"1f", "2f", "3f", "4f"}, "花槓 春夏秋冬"),
        ({"5f", "6f", "7f", "8f"}, "花槓 梅蘭竹菊"),
    )
    matching_flowers = {
        0: {"1f": "正花 春", "5f": "正花 梅"},
        1: {"2f": "正花 夏", "6f": "正花 蘭"},
        2: {"3f": "正花 秋", "8f": "正花 菊"},
        3: {"4f": "正花 冬", "7f": "正花 竹"},
    }.get(seat_wind, {})

    for group_tiles, group_name in groups:
        if group_tiles.issubset(flower_set):
            result.append((group_name, 2))
            continue
        for tile, name in matching_flowers.items():
            if tile in group_tiles and tile in flower_set:
                result.append((name, 1))
    return result


def _color_fan(tiles: List[str], melds: List[Dict]) -> Optional[Dict]:
    suits = set()
    has_honors = False

    for tile in tiles:
        if is_flower(tile):
            continue
        if tile.endswith("z"):
            has_honors = True
        else:
            suits.add(tile[-1])

    for meld in melds:
        for tile in meld.get("tiles", [meld.get("tile")]):
            if not tile or tile == "BACK":
                continue
            if tile.endswith("z"):
                has_honors = True
            elif not is_flower(tile):
                suits.add(tile[-1])

    if len(suits) != 1:
        return None
    if has_honors:
        return {"name": "混一色", "value": 4}
    return {"name": "清一色", "value": 8}


def _is_all_triplets(tiles: List[str], melds: List[Dict]) -> bool:
    if any(meld.get("type") == "CHI" for meld in melds):
        return False

    counts = Counter(tiles)
    pair_count = 0

    for count in counts.values():
        remainder = count % 3
        if remainder == 0:
            continue
        if remainder == 2:
            pair_count += 1
            continue
        return False

    return pair_count == 1


def _set_counts(tiles: List[str], melds: List[Dict]) -> Counter:
    counts = Counter(tiles)
    for meld in melds:
        meld_type = meld.get("type")
        if meld_type == "PON":
            counts[meld["tile"]] += 3
        elif meld_type in {"KANG", "ANKANG", "BUKANG"}:
            counts[meld["tile"]] += 4
    return counts
