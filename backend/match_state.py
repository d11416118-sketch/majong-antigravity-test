import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


MATCH_ACTIVE = "ACTIVE"
MATCH_ENDED = "ENDED"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


@dataclass
class MatchState:
    ruleset_id: str
    player_uids: List[str]
    match_id: str = field(default_factory=lambda: _new_id("match"))
    hand_id: Optional[str] = None
    hand_number: int = 0
    state: str = MATCH_ACTIVE
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    cumulative_scores: Dict[str, int] = field(default_factory=dict)
    completed_hands: List[Dict] = field(default_factory=list)
    final_ranks: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.cumulative_scores:
            self.cumulative_scores = {uid: 0 for uid in self.player_uids}

    def begin_hand(self) -> str:
        if self.state == MATCH_ENDED:
            raise ValueError("整場牌局已結束")
        self.hand_number += 1
        self.hand_id = _new_id("hand")
        return self.hand_id

    def apply_score_deltas(self, score_deltas: Dict[str, int]) -> None:
        for uid in self.player_uids:
            self.cumulative_scores[uid] = int(self.cumulative_scores.get(uid, 0)) + int(score_deltas.get(uid, 0))

    def record_hand(self, hand_result: Dict) -> None:
        self.completed_hands.append(hand_result)

    def finish(self, seat_by_uid: Dict[str, int]) -> Dict[str, int]:
        ranked = sorted(
            self.player_uids,
            key=lambda uid: (-int(self.cumulative_scores.get(uid, 0)), int(seat_by_uid.get(uid, 99))),
        )
        self.final_ranks = {uid: index + 1 for index, uid in enumerate(ranked)}
        self.state = MATCH_ENDED
        self.ended_at = time.time()
        return dict(self.final_ranks)

    @property
    def ended(self) -> bool:
        return self.state == MATCH_ENDED

    def to_snapshot(self) -> Dict:
        return {
            "match_id": self.match_id,
            "hand_id": self.hand_id,
            "hand_number": self.hand_number,
            "state": self.state,
            "ruleset_id": self.ruleset_id,
            "cumulative_scores": dict(self.cumulative_scores),
            "completed_hand_count": len(self.completed_hands),
            "final_ranks": dict(self.final_ranks),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }
