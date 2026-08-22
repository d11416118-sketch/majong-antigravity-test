from typing import Dict, List

from .rules import tile_sort_key


class Player:
    def __init__(self, uid: str, name: str, idx: int):
        self.uid = uid
        self.name = name
        self.idx = idx
        self.score = 0
        self.hand: List[str] = []
        self.flowers: List[str] = []
        self.melds: List[Dict] = []
        self.discards: List[str] = []
        self.guoshui = False
        self.guoshui_safe_draw = False
        self.declared_ting = False
        self.di_ting = False
        self.di_ting_valid = False

    def reset_for_new_round(self) -> None:
        self.hand = []
        self.flowers = []
        self.melds = []
        self.discards = []
        self.guoshui = False
        self.guoshui_safe_draw = False
        self.declared_ting = False
        self.di_ting = False
        self.di_ting_valid = False

    @property
    def graveyard(self) -> List[str]:
        return self.discards

    def add_tile(self, tile: str) -> None:
        self.hand.append(tile)
        self.sort_hand()

    def remove_tile(self, tile: str) -> bool:
        if tile not in self.hand:
            return False
        self.hand.remove(tile)
        return True

    def sort_hand(self) -> None:
        self.hand.sort(key=tile_sort_key)

    def to_public_json(self) -> Dict:
        return {
            "uid": self.uid,
            "idx": self.idx,
            "name": self.name,
            "score": self.score,
            "hand_count": len(self.hand),
            "flowers": list(self.flowers),
            "melds": [dict(m) for m in self.melds],
            "discards": list(self.discards),
            "declared_ting": self.declared_ting,
            "di_ting": self.di_ting and self.di_ting_valid,
        }

    def to_private_json(self) -> Dict:
        data = self.to_public_json()
        data["hand"] = list(self.hand)
        data["guoshui"] = self.guoshui
        data["guoshui_safe_draw"] = self.guoshui_safe_draw
        return data
