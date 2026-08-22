from collections import Counter
from typing import Dict, List, Optional

from .rules import FLOWERS, all_play_tiles


ALL_TILE_COUNTS = {tile: 4 for tile in all_play_tiles()}
ALL_TILE_COUNTS.update({tile: 1 for tile in FLOWERS})
TILE_ORDER = list(all_play_tiles()) + list(FLOWERS)


def build_visible_state(game, uid: str, available_actions: Optional[List[Dict]] = None, compact: bool = False) -> Dict:
    snapshot = game.get_snapshot(uid)
    self_player = next((player for player in snapshot["players"] if player["uid"] == uid), {})
    players = [_public_player_view(player, uid) for player in snapshot["players"]]
    history = visible_history(game, uid)
    tracker = build_tile_tracker(snapshot["players"], uid)

    base = {
        "state": snapshot["state"],
        "dealer_idx": snapshot["dealer_idx"],
        "round_wind": snapshot["round_wind"],
        "current_turn_idx": snapshot["current_turn_idx"],
        "wall_remaining_count": snapshot["wall_remaining_count"],
        "last_discard": snapshot["last_discard"],
        "self": {
            "idx": self_player.get("idx"),
            "hand": self_player.get("hand", []),
            "flowers": self_player.get("flowers", []),
            "melds": self_player.get("melds", []),
            "guoshui": bool(self_player.get("guoshui")),
            "declared_ting": bool(self_player.get("declared_ting")),
            "di_ting": bool(self_player.get("di_ting")),
        },
        "players": players,
        "available_actions": available_actions or [],
        "history": history[-80:],
        "tile_tracker": tracker,
    }

    if not compact:
        return base

    return {
        "state": base["state"],
        "dealer_idx": base["dealer_idx"],
        "round_wind": base["round_wind"],
        "current_turn_idx": base["current_turn_idx"],
        "wall_remaining_count": base["wall_remaining_count"],
        "last_discard": base["last_discard"],
        "self": {
            "idx": base["self"]["idx"],
            "hand": [{"i": index, "tile": tile} for index, tile in enumerate(base["self"]["hand"])],
            "flowers": base["self"]["flowers"],
            "melds": base["self"]["melds"],
            "guoshui": base["self"]["guoshui"],
            "declared_ting": base["self"]["declared_ting"],
            "di_ting": base["self"]["di_ting"],
        },
        "players": [
            {
                "idx": player["idx"],
                "name": player["name"],
                "hand_count": player["hand_count"],
                "flowers": player["flowers"],
                "melds": player["melds"],
                "discards": player["discards"],
                "declared_ting": player["declared_ting"],
                "di_ting": player["di_ting"],
            }
            for player in players
        ],
        "available_actions": available_actions or [],
        "history_tail": history[-30:],
        "unknown_tile_counts": {item["tile"]: item["unknown_count"] for item in tracker},
    }


def build_tile_tracker(players: List[Dict], uid: str) -> List[Dict]:
    known = Counter()
    for player in players:
        if player["uid"] == uid:
            known.update(player.get("hand", []))
        known.update(player.get("flowers", []))
        known.update(player.get("discards", player.get("graveyard", [])))
        for meld in player.get("melds", []):
            if meld.get("concealed") and player["uid"] == uid:
                known[meld["tile"]] += 4
                continue
            for tile in meld.get("tiles", [meld.get("tile")]):
                if tile and tile != "BACK":
                    known[tile] += 1

    tracker = []
    for tile in TILE_ORDER:
        total = ALL_TILE_COUNTS[tile]
        known_count = min(total, known.get(tile, 0))
        tracker.append(
            {
                "tile": tile,
                "total": total,
                "known_count": known_count,
                "unknown_count": max(0, total - known_count),
            }
        )
    return tracker


def visible_history(game, uid: str) -> List[Dict]:
    result = []
    for event in getattr(game, "event_log", []):
        item = {key: value for key, value in event.items() if key != "uid"}
        if event.get("type") == "DRAW" and event.get("uid") != uid:
            item["tile"] = None
            item["hidden"] = True
        if event.get("type") == "TING" and event.get("uid") != uid:
            item.pop("ting_tiles", None)
        result.append(item)
    return result


def _public_player_view(player: Dict, uid: str) -> Dict:
    item = {
        "uid": player["uid"],
        "idx": player["idx"],
        "name": player["name"],
        "score": player["score"],
        "hand_count": player.get("hand_count", len(player.get("hand", []))),
        "flowers": player.get("flowers", []),
        "melds": player.get("melds", []),
        "discards": player.get("discards", player.get("graveyard", [])),
        "declared_ting": bool(player.get("declared_ting")),
        "di_ting": bool(player.get("di_ting")),
    }
    if player["uid"] == uid:
        item["hand"] = player.get("hand", [])
    return item
