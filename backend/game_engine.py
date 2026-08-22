import random
from typing import Dict, List, Optional

from .match_state import MatchState
from .player import Player
from .rule_profile import RuleProfile, STANDARD_TW16_V1
from .rules import (
    build_wall,
    calculate_fan,
    can_chi,
    can_hu,
    can_kang,
    can_pon,
    get_ting_tiles,
    is_flower,
    resolve_actions,
    ACTION_PRIORITY,
    tile_sort_key,
)
from .settlement import settle_single_winner


STATE_IDLE = "IDLE"
STATE_PLAYER_TURN = "PLAYER_TURN"
STATE_WAIT_RESPONSE = "WAIT_RESPONSE"
STATE_END_GAME = "END_GAME"

DEAD_WALL_SIZE = 16


class GameEngine:
    def __init__(self, rules: RuleProfile = STANDARD_TW16_V1):
        self.rules = rules
        self.state = STATE_IDLE
        self.players: List[Player] = []
        self.room_id = ""
        self.wall: List[str] = []
        self.dealer_idx = 0
        self.current_turn_idx = 0
        self.round_wind = 0
        self.lian_zhuang = 0
        self.last_discard: Optional[Dict] = None
        self.pending_uids: set[str] = set()
        self.pending_actions_by_uid: Dict[str, List[Dict]] = {}
        self.response_queue: List[Dict] = []
        self.winner_uid: Optional[str] = None
        self.score_breakdown: Optional[Dict] = None
        self.game_over_payload: Optional[Dict] = None
        self.last_drawn_tile: Optional[str] = None
        self.last_draw_context: Optional[str] = None
        self.last_replacement_source: Optional[str] = None
        self.last_draw_is_haidi = False
        self.event_log: List[Dict] = []
        self.event_seq = 0
        self.match: Optional[MatchState] = None
        self.pending_kang: Optional[Dict] = None
        self.pending_flower_win: Optional[Dict] = None
        self.normal_draw_counts: Dict[str, int] = {}
        self.discard_counts: Dict[str, int] = {}
        self.total_discard_count = 0
        self.opening_claim_occurred = False

    def add_player(self, uid: str, name: str) -> bool:
        if len(self.players) >= 4 or self.get_player(uid):
            return False
        self.players.append(Player(uid, name, len(self.players)))
        return True

    def get_player(self, uid: str) -> Optional[Player]:
        return next((player for player in self.players if player.uid == uid), None)

    def start_game(self) -> None:
        self.dealer_idx = 0
        self.round_wind = 0
        self.lian_zhuang = 0
        for player in self.players:
            player.score = 0
        self.match = MatchState(self.rules.id, [player.uid for player in self.players])
        self.start_round()

    def start_round(self) -> None:
        if not self.match:
            self.match = MatchState(self.rules.id, [player.uid for player in self.players])
        self.match.begin_hand()
        self.wall = build_wall()
        random.shuffle(self.wall)
        self.state = STATE_PLAYER_TURN
        self.last_discard = None
        self.pending_uids = set()
        self.pending_actions_by_uid = {}
        self.response_queue = []
        self.winner_uid = None
        self.score_breakdown = None
        self.game_over_payload = None
        self.last_drawn_tile = None
        self.last_draw_context = None
        self.last_replacement_source = None
        self.last_draw_is_haidi = False
        self.event_log = []
        self.event_seq = 0
        self.pending_kang = None
        self.pending_flower_win = None
        self.normal_draw_counts = {player.uid: 0 for player in self.players}
        self.discard_counts = {player.uid: 0 for player in self.players}
        self.total_discard_count = 0
        self.opening_claim_occurred = False

        for player in self.players:
            player.reset_for_new_round()

        for _ in range(16):
            for player in self.players:
                self._draw_until_play_tile(player, from_back=False, log_event=False, draw_context="INITIAL")

        self.current_turn_idx = self.dealer_idx
        self._draw_until_play_tile(
            self.players[self.dealer_idx],
            from_back=False,
            log_event=False,
            draw_context="INITIAL",
        )
        self._record_event("ROUND_START", player_idx=self.dealer_idx)

    def discard_tile(
        self,
        uid: str,
        tile_index: int,
        ting_declaration: bool = False,
    ) -> List[Dict]:
        if self.state != STATE_PLAYER_TURN:
            raise ValueError("現在不能出牌")

        player = self.players[self.current_turn_idx]
        if player.uid != uid:
            raise ValueError("還沒輪到你")

        if tile_index < 0 or tile_index >= len(player.hand):
            raise ValueError("出牌索引無效")
        if (
            player.declared_ting
            and not ting_declaration
            and self.last_drawn_tile
            and player.hand[tile_index] != self.last_drawn_tile
        ):
            raise ValueError("宣告聽牌後只能摸什麼打什麼")

        can_clear_guoshui = player.guoshui and player.guoshui_safe_draw
        if not player.guoshui and can_hu(player.hand, None, len(player.melds)):
            self._mark_guoshui(player)

        tile = player.hand.pop(tile_index)
        player.sort_hand()
        player.discards.append(tile)
        self.total_discard_count += 1
        self.discard_counts[player.uid] = int(self.discard_counts.get(player.uid, 0)) + 1
        is_hedi = self.last_draw_context == "NORMAL" and self.last_draw_is_haidi
        self.last_discard = {
            "tile": tile,
            "from_idx": player.idx,
            "is_hedi": is_hedi,
            "discard_number": self.total_discard_count,
        }
        self._record_event(
            "DISCARD",
            uid=player.uid,
            player_idx=player.idx,
            tile=tile,
            is_hedi=is_hedi,
        )
        if can_clear_guoshui:
            self._clear_guoshui(player)

        actions = self.check_actions_for_discard(tile, player.idx)
        if actions:
            self.state = STATE_WAIT_RESPONSE
            self.pending_uids = {action["uid"] for action in actions}
            self.pending_actions_by_uid = self._group_actions_by_uid(actions)
            self.response_queue = []
        else:
            self.pending_actions_by_uid = {}
        return actions

    def ting_discard_options(self, player_idx: Optional[int] = None) -> List[Dict]:
        idx = self.current_turn_idx if player_idx is None else player_idx
        player = self.players[idx]
        if player.declared_ting:
            return []

        options = []
        seen = set()
        for tile_index, tile in enumerate(player.hand):
            if tile in seen:
                continue
            seen.add(tile)
            after_discard = list(player.hand)
            after_discard.pop(tile_index)
            ting_tiles = get_ting_tiles(after_discard, player.melds)
            if not ting_tiles:
                continue
            options.append(
                {
                    "uid": player.uid,
                    "type": "TING",
                    "tile": tile,
                    "tile_index": tile_index,
                    "ting_tiles": ting_tiles,
                }
            )
        return options

    def declare_ting(self, uid: str, tile_index: int) -> List[Dict]:
        if self.state != STATE_PLAYER_TURN:
            raise ValueError("現在不能宣告聽牌")
        player = self.get_player(uid)
        if not player or player.idx != self.current_turn_idx:
            raise ValueError("還沒輪到你")
        option = next(
            (
                item
                for item in self.ting_discard_options(player.idx)
                if int(item["tile_index"]) == int(tile_index)
            ),
            None,
        )
        if not option:
            raise ValueError("打出這張牌後沒有聽牌")

        player.declared_ting = True
        player.di_ting = (
            self.discard_counts.get(player.uid, 0) == 0
            and not player.melds
        )
        player.di_ting_valid = player.di_ting
        self._record_event(
            "TING",
            uid=player.uid,
            player_idx=player.idx,
            tile=option["tile"],
            ting_tiles=list(option["ting_tiles"]),
            di_ting=player.di_ting,
        )
        return self.discard_tile(uid, tile_index, ting_declaration=True)

    def check_actions_for_discard(self, tile: str, from_idx: int) -> List[Dict]:
        actions: List[Dict] = []

        for player in self.players:
            if player.idx == from_idx:
                continue

            if not player.guoshui and can_hu(player.hand, tile, len(player.melds)):
                actions.append(
                    {
                        "uid": player.uid,
                        "type": "HU",
                        "tile": tile,
                        "from_idx": from_idx,
                        "player_idx": player.idx,
                        "is_hedi": bool(self.last_discard and self.last_discard.get("is_hedi")),
                    }
                )

            if not player.declared_ting and can_pon(player.hand, tile):
                actions.append({"uid": player.uid, "type": "PON", "tile": tile, "from_idx": from_idx, "player_idx": player.idx})

            if can_kang(player.hand, tile):
                actions.append({"uid": player.uid, "type": "KANG", "tile": tile, "from_idx": from_idx, "player_idx": player.idx})

            if not player.declared_ting and player.idx == (from_idx + 1) % 4:
                for pair in can_chi(player.hand, tile):
                    actions.append(
                        {
                            "uid": player.uid,
                            "type": "CHI",
                            "tile": tile,
                            "tiles": pair,
                            "from_idx": from_idx,
                            "player_idx": player.idx,
                        }
                    )

        return actions

    def submit_response(self, uid: str, action_type: str, tile: Optional[str] = None, tiles: Optional[List[str]] = None) -> Dict:
        if self.state != STATE_WAIT_RESPONSE:
            return {"status": "IGNORED"}

        if uid not in self.pending_uids:
            return {"status": "IGNORED"}

        response = self._build_response(uid, action_type or "PASS", tile, tiles)
        if response.get("type") != "HU" and any(
            action.get("type") == "HU"
            for action in self.pending_actions_by_uid.get(uid, [])
        ):
            player = self.get_player(uid)
            if player:
                self._mark_guoshui(player)
        self.response_queue = [queued for queued in self.response_queue if queued["uid"] != uid]
        self.response_queue.append(response)

        responded = {response["uid"] for response in self.response_queue}
        if not self._can_resolve_responses(responded):
            return {"status": "WAITING"}

        pending_kang = dict(self.pending_kang) if self.pending_kang else None
        from_idx = (
            pending_kang["player_idx"]
            if pending_kang
            else (self.last_discard["from_idx"] if self.last_discard else None)
        )
        winner = resolve_actions(self.response_queue, from_idx)
        self.pending_uids = set()
        self.pending_actions_by_uid = {}
        self.response_queue = []

        if winner:
            if pending_kang:
                winner.setdefault("tile", pending_kang["tile"])
                winner.setdefault("from_idx", pending_kang["player_idx"])
                winner["is_qiang_gang"] = True
                self.pending_kang = None
            elif self.last_discard:
                winner.setdefault("tile", self.last_discard["tile"])
                winner.setdefault("from_idx", self.last_discard["from_idx"])
            return {"status": "ACTION", "action": winner}

        if pending_kang:
            result = self._complete_bukang(pending_kang)
            self.pending_kang = None
            return result

        return {"status": "PASS"}

    def apply_claim_action(self, action: Dict) -> Dict:
        action_type = action["type"]
        player = self.get_player(action["uid"])
        if not player:
            raise ValueError("找不到玩家")

        if action_type == "HU":
            if player.guoshui:
                raise ValueError("目前處於過水狀態，不能胡牌")
            return self._finish_hu(action)

        if not self.last_discard:
            raise ValueError("沒有可鳴的棄牌")

        tile = action.get("tile") or self.last_discard["tile"]
        from_idx = action.get("from_idx", self.last_discard["from_idx"])
        self._remove_claimed_discard(from_idx, tile)

        if action_type == "CHI":
            self.opening_claim_occurred = True
            pair = action.get("tiles") or []
            if len(pair) != 2:
                pair = self._choose_chi_pair(player, tile)
            for needed in pair:
                if not player.remove_tile(needed):
                    raise ValueError("吃牌組合不存在")
            meld_tiles = sorted([pair[0], tile, pair[1]], key=tile_sort_key)
            player.melds.append({"type": "CHI", "tile": tile, "tiles": meld_tiles, "from_idx": from_idx})
            self._record_event("CLAIM", uid=player.uid, player_idx=player.idx, action="CHI", tile=tile, tiles=meld_tiles, from_idx=from_idx)
            self._enter_player_turn(player.idx)
            return {"status": "CLAIM", "type": "CHI"}

        if action_type == "PON":
            self.opening_claim_occurred = True
            for _ in range(2):
                if not player.remove_tile(tile):
                    raise ValueError("碰牌數量不足")
            player.melds.append({"type": "PON", "tile": tile, "tiles": [tile, tile, tile], "from_idx": from_idx})
            self._record_event("CLAIM", uid=player.uid, player_idx=player.idx, action="PON", tile=tile, tiles=[tile, tile, tile], from_idx=from_idx)
            self._enter_player_turn(player.idx)
            return {"status": "CLAIM", "type": "PON"}

        if action_type == "KANG":
            self.opening_claim_occurred = True
            for _ in range(3):
                if not player.remove_tile(tile):
                    raise ValueError("明槓牌數量不足")
            player.melds.append({"type": "KANG", "tile": tile, "tiles": [tile] * 4, "from_idx": from_idx})
            self._record_event("CLAIM", uid=player.uid, player_idx=player.idx, action="KANG", tile=tile, tiles=[tile] * 4, from_idx=from_idx)
            self._enter_player_turn(player.idx)
            self.draw_replacement_tile(player, source="MINGKANG")
            flower_result = self.resolve_pending_flower_win(player.idx)
            if flower_result:
                return flower_result
            return {"status": "CLAIM", "type": "KANG"}

        raise ValueError("未知動作")

    def apply_self_action(self, uid: str, action_type: str, tile: Optional[str]) -> Dict:
        if self.state != STATE_PLAYER_TURN:
            return {"status": "IGNORED"}

        player = self.get_player(uid)
        if not player or player.idx != self.current_turn_idx:
            return {"status": "IGNORED"}

        if action_type == "HU":
            if player.guoshui:
                raise ValueError("目前處於過水狀態，不能自摸")
            if not self._can_self_draw_current_tile(player):
                raise ValueError("宣告聽牌後，明槓補牌不可自摸")
            return self._finish_hu({"uid": uid, "type": "HU", "tile": tile or self.last_drawn_tile or (player.hand[-1] if player.hand else None), "from_idx": player.idx, "is_zimo": True})

        if action_type == "ANKANG":
            if not tile or player.hand.count(tile) < 4:
                raise ValueError("暗槓牌數量不足")
            for _ in range(4):
                player.remove_tile(tile)
            player.melds.append(
                {"type": "ANKANG", "tile": tile, "tiles": ["BACK", tile, tile, "BACK"], "from_idx": player.idx, "concealed": True}
            )
            self.opening_claim_occurred = True
            self._record_event("SELF_ACTION", uid=player.uid, player_idx=player.idx, action="ANKANG", tile=tile, tiles=["BACK", tile, tile, "BACK"])
            self._enter_player_turn(player.idx)
            self.draw_replacement_tile(player, source="ANKANG")
            flower_result = self.resolve_pending_flower_win(player.idx)
            if flower_result:
                return flower_result
            return {"status": "SELF_ACTION", "type": "ANKANG"}

        if action_type == "BUKANG":
            if not tile:
                raise ValueError("補槓缺少牌")
            pon = next((meld for meld in player.melds if meld["type"] == "PON" and meld["tile"] == tile), None)
            if not pon or tile not in player.hand:
                raise ValueError("補槓條件不足")
            qiang_gang_actions = self._qiang_gang_actions(player, tile)
            if qiang_gang_actions:
                self.pending_kang = {
                    "uid": player.uid,
                    "player_idx": player.idx,
                    "tile": tile,
                }
                self.state = STATE_WAIT_RESPONSE
                self.pending_uids = {action["uid"] for action in qiang_gang_actions}
                self.pending_actions_by_uid = self._group_actions_by_uid(qiang_gang_actions)
                self.response_queue = []
                return {
                    "status": "WAIT_RESPONSE",
                    "type": "BUKANG",
                    "actions": qiang_gang_actions,
                }
            return self._complete_bukang({"uid": player.uid, "player_idx": player.idx, "tile": tile})

        return {"status": "IGNORED"}

    def advance_turn(self) -> Dict:
        next_idx = (self.current_turn_idx + 1) % 4
        if len(self.wall) <= self.rules.dead_wall_size:
            return self._finish_draw()

        self.current_turn_idx = next_idx
        self._enter_player_turn(next_idx)
        self._draw_until_play_tile(
            self.players[next_idx],
            from_back=False,
            draw_context="NORMAL",
        )
        flower_result = self.resolve_pending_flower_win(next_idx)
        if flower_result:
            return flower_result
        return {"status": "DRAW", "player_idx": next_idx}

    def check_self_actions(self, player_idx: Optional[int] = None) -> List[Dict]:
        idx = self.current_turn_idx if player_idx is None else player_idx
        player = self.players[idx]
        actions: List[Dict] = []

        if (
            not player.guoshui
            and self._can_self_draw_current_tile(player)
            and can_hu(player.hand, None, len(player.melds))
        ):
            actions.append({"uid": player.uid, "type": "HU", "tile": self.last_drawn_tile or (player.hand[-1] if player.hand else None)})

        counts = {tile: player.hand.count(tile) for tile in set(player.hand) if not is_flower(tile)}
        for tile, count in counts.items():
            if count == 4:
                actions.append({"uid": player.uid, "type": "ANKANG", "tile": tile})

        for meld in player.melds:
            if meld["type"] == "PON" and meld["tile"] in player.hand:
                actions.append({"uid": player.uid, "type": "BUKANG", "tile": meld["tile"]})

        actions.extend(self.ting_discard_options(idx))
        return actions

    def draw_replacement_tile(self, player: Player, source: str) -> Optional[str]:
        return self._draw_until_play_tile(
            player,
            from_back=True,
            draw_context="REPLACEMENT",
            replacement_source=source,
        )

    def get_snapshot(self, recipient_uid: str) -> Dict:
        players = [
            player.to_private_json() if player.uid == recipient_uid else player.to_public_json()
            for player in self.players
        ]
        snapshot = {
            "room_id": self.room_id,
            "state": self.state,
            "dealer_idx": self.dealer_idx,
            "current_turn_idx": self.current_turn_idx,
            "round_wind": self.round_wind,
            "lian_zhuang": self.lian_zhuang,
            "wall_remaining_count": len(self.wall),
            "dead_wall_count": self.rules.dead_wall_size,
            "last_discard": self.last_discard,
            "players": players,
            "winner_uid": self.winner_uid,
            "score_breakdown": self.score_breakdown,
            "rules": self.rules.to_public_json(),
            "turn_prompt": self._build_turn_prompt(recipient_uid),
        }
        if self.match:
            snapshot["match"] = self.match.to_snapshot()
        return snapshot

    def _build_turn_prompt(self, recipient_uid: str) -> Dict:
        if self.state == STATE_PLAYER_TURN and self.players:
            player = self.players[self.current_turn_idx]
            return {
                "phase": "DISCARD",
                "actor_uids": [player.uid],
                "actor_names": [player.name],
                "is_recipient": player.uid == recipient_uid,
                "recipient_actions": [],
            }

        if self.state == STATE_WAIT_RESPONSE:
            responded_uids = {response["uid"] for response in self.response_queue}
            waiting_players = [
                player
                for player in self.players
                if player.uid in self.pending_uids and player.uid not in responded_uids
            ]
            recipient_waiting = any(player.uid == recipient_uid for player in waiting_players)
            recipient_actions = []
            if recipient_waiting:
                recipient_actions = sorted(
                    {
                        action.get("type", "")
                        for action in self.pending_actions_by_uid.get(recipient_uid, [])
                        if action.get("type")
                    },
                    key=lambda action_type: (-ACTION_PRIORITY.get(action_type, 0), action_type),
                )
            return {
                "phase": "RESPONSE",
                "actor_uids": [player.uid for player in waiting_players],
                "actor_names": [player.name for player in waiting_players],
                "is_recipient": recipient_waiting,
                "recipient_actions": recipient_actions,
            }

        return {
            "phase": "END" if self.state == STATE_END_GAME else "IDLE",
            "actor_uids": [],
            "actor_names": [],
            "is_recipient": False,
            "recipient_actions": [],
        }

    def _draw_until_play_tile(
        self,
        player: Player,
        from_back: bool,
        log_event: bool = True,
        draw_context: str = "NORMAL",
        replacement_source: Optional[str] = None,
    ) -> Optional[str]:
        current_context = draw_context
        current_replacement_source = (
            replacement_source if draw_context == "REPLACEMENT" else None
        )
        if draw_context == "NORMAL":
            self.normal_draw_counts[player.uid] = int(self.normal_draw_counts.get(player.uid, 0)) + 1
        while self.wall:
            is_haidi = (
                current_context == "NORMAL"
                and not from_back
                and len(self.wall) == self.rules.dead_wall_size + 1
            )
            tile = self.wall.pop() if from_back else self.wall.pop(0)
            if is_flower(tile):
                player.flowers.append(tile)
                self._update_flower_win_candidate(player, initial=not log_event)
                if log_event:
                    self._record_event("FLOWER", uid=player.uid, player_idx=player.idx, tile=tile)
                from_back = True
                current_context = "REPLACEMENT"
                if current_replacement_source is None:
                    current_replacement_source = "FLOWER"
                continue
            player.add_tile(tile)
            self.last_drawn_tile = tile
            self.last_draw_context = current_context
            self.last_replacement_source = (
                current_replacement_source
                if current_context == "REPLACEMENT"
                else None
            )
            self.last_draw_is_haidi = is_haidi
            if player.guoshui:
                player.guoshui_safe_draw = not can_hu(player.hand, None, len(player.melds))
            if log_event:
                self._record_event(
                    "DRAW",
                    uid=player.uid,
                    player_idx=player.idx,
                    tile=tile,
                    from_back=from_back,
                    draw_context=current_context,
                    replacement_source=self.last_replacement_source,
                    is_haidi=is_haidi,
                )
            return tile
        return None

    def _update_flower_win_candidate(self, drawer: Player, initial: bool) -> None:
        if self.pending_flower_win:
            return
        flower_total = sum(len(player.flowers) for player in self.players)
        if flower_total < 8:
            return

        eight_holder = next((player for player in self.players if len(player.flowers) == 8), None)
        if eight_holder:
            self.pending_flower_win = {
                "type": "BAXIAN",
                "winner_uid": eight_holder.uid,
                "payer_uid": None,
                "initial": bool(initial),
            }
            return

        seven_holder = next((player for player in self.players if len(player.flowers) == 7), None)
        one_holder = next((player for player in self.players if len(player.flowers) == 1), None)
        if seven_holder and one_holder:
            self.pending_flower_win = {
                "type": "QIQIANGYI",
                "winner_uid": seven_holder.uid,
                "payer_uid": one_holder.uid,
                "initial": bool(initial),
            }

    def resolve_pending_flower_win(self, player_idx: Optional[int] = None) -> Optional[Dict]:
        pending = self.pending_flower_win
        if not pending:
            return None

        winner = self.get_player(pending["winner_uid"])
        if not winner:
            self.pending_flower_win = None
            return None
        payer = self.get_player(pending.get("payer_uid")) if pending.get("payer_uid") else None
        can_combine_hand = (
            winner.idx == self.current_turn_idx
            and self._can_self_draw_current_tile(winner)
            and can_hu(
                winner.hand,
                None,
                len(winner.melds),
            )
        )
        flower_type = pending["type"]
        self.pending_flower_win = None
        return self._finish_hu(
            {
                "uid": winner.uid,
                "type": "HU",
                "tile": self.last_drawn_tile if can_combine_hand else None,
                "from_idx": winner.idx if can_combine_hand or flower_type == "BAXIAN" else (payer.idx if payer else None),
                "is_zimo": bool(can_combine_hand or flower_type == "BAXIAN"),
                "flower_win_type": flower_type,
                "flower_payer_uid": payer.uid if payer else None,
                "flower_only": not can_combine_hand,
            }
        )

    def _enter_player_turn(self, idx: int) -> None:
        self.state = STATE_PLAYER_TURN
        self.current_turn_idx = idx
        self.pending_uids = set()
        self.pending_actions_by_uid = {}
        self.response_queue = []
        self.last_discard = None
        self.last_drawn_tile = None
        self.last_draw_context = None
        self.last_replacement_source = None
        self.last_draw_is_haidi = False

    def _can_self_draw_current_tile(self, player: Player) -> bool:
        return not (
            player.declared_ting
            and self.last_draw_context == "REPLACEMENT"
            and self.last_replacement_source == "MINGKANG"
        )

    def _group_actions_by_uid(self, actions: List[Dict]) -> Dict[str, List[Dict]]:
        actions_by_uid: Dict[str, List[Dict]] = {}
        for action in actions:
            actions_by_uid.setdefault(action["uid"], []).append(action)
        return actions_by_uid

    def _build_response(self, uid: str, action_type: str, tile: Optional[str], tiles: Optional[List[str]]) -> Dict:
        if action_type == "PASS":
            return {"uid": uid, "type": "PASS"}

        legal_actions = self.pending_actions_by_uid.get(uid, [])
        for action in legal_actions:
            if action["type"] != action_type:
                continue
            if tile and action.get("tile") != tile:
                continue
            if action_type == "CHI" and not self._same_tiles(action.get("tiles", []), tiles or []):
                continue

            return dict(action)

        raise ValueError("Action is not available")

    def _same_tiles(self, left: List[str], right: List[str]) -> bool:
        return sorted(left, key=tile_sort_key) == sorted(right, key=tile_sort_key)

    def _can_resolve_responses(self, responded: set[str]) -> bool:
        if any(response.get("type") == "HU" for response in self.response_queue):
            return True

        if self.pending_uids.issubset(responded):
            return True

        claimed_priorities = [
            ACTION_PRIORITY.get(response.get("type", "PASS"), 0)
            for response in self.response_queue
            if response.get("type") != "PASS"
        ]
        if not claimed_priorities:
            return False

        highest_claimed = max(claimed_priorities)
        waiting_uids = self.pending_uids - responded
        highest_waiting = max(
            (
                ACTION_PRIORITY.get(action.get("type", "PASS"), 0)
                for uid in waiting_uids
                for action in self.pending_actions_by_uid.get(uid, [])
            ),
            default=0,
        )
        return highest_claimed > highest_waiting

    def _remove_claimed_discard(self, from_idx: int, tile: str) -> None:
        discards = self.players[from_idx].discards
        if discards and discards[-1] == tile:
            discards.pop()
        elif tile in discards:
            discards.remove(tile)

    def _choose_chi_pair(self, player: Player, tile: str) -> List[str]:
        choices = can_chi(player.hand, tile)
        if not choices:
            raise ValueError("沒有可吃的組合")
        return choices[0]

    def _mark_guoshui(self, player: Player) -> None:
        player.guoshui = True
        player.guoshui_safe_draw = False
        if player.di_ting:
            player.di_ting_valid = False

    def _clear_guoshui(self, player: Player) -> None:
        player.guoshui = False
        player.guoshui_safe_draw = False

    def _qiang_gang_actions(self, kang_player: Player, tile: str) -> List[Dict]:
        actions = []
        for player in self.players:
            if player.idx == kang_player.idx or player.guoshui:
                continue
            if can_hu(player.hand, tile, len(player.melds)):
                actions.append(
                    {
                        "uid": player.uid,
                        "type": "HU",
                        "tile": tile,
                        "from_idx": kang_player.idx,
                        "player_idx": player.idx,
                        "is_qiang_gang": True,
                    }
                )
        return actions

    def _complete_bukang(self, pending_kang: Dict) -> Dict:
        player = self.get_player(pending_kang["uid"])
        tile = pending_kang["tile"]
        if not player:
            raise ValueError("找不到補槓玩家")
        pon = next(
            (meld for meld in player.melds if meld["type"] == "PON" and meld["tile"] == tile),
            None,
        )
        if not pon or not player.remove_tile(tile):
            raise ValueError("補槓條件不足")
        pon["type"] = "BUKANG"
        pon["tiles"] = [tile] * 4
        self.opening_claim_occurred = True
        self._record_event(
            "SELF_ACTION",
            uid=player.uid,
            player_idx=player.idx,
            action="BUKANG",
            tile=tile,
            tiles=[tile] * 4,
        )
        self._enter_player_turn(player.idx)
        self.draw_replacement_tile(player, source="BUKANG")
        flower_result = self.resolve_pending_flower_win(player.idx)
        if flower_result:
            return flower_result
        return {"status": "SELF_ACTION", "type": "BUKANG"}

    def _record_event(self, event_type: str, **payload) -> None:
        self.event_seq += 1
        self.event_log.append({"seq": self.event_seq, "type": event_type, **payload})
        if len(self.event_log) > 240:
            self.event_log = self.event_log[-240:]

    def _finish_hu(self, action: Dict) -> Dict:
        player = self.get_player(action["uid"])
        if not player:
            raise ValueError("找不到胡牌玩家")

        tile = action.get("tile")
        from_idx = action.get("from_idx")
        is_zimo = bool(action.get("is_zimo") or from_idx == player.idx)
        payer = next((candidate for candidate in self.players if candidate.idx == from_idx), None)
        flower_win_type = action.get("flower_win_type")
        flower_only = bool(action.get("flower_only"))
        flower_payer_uid = action.get("flower_payer_uid")
        is_qiang_gang = bool(action.get("is_qiang_gang"))
        is_gang_shang = not flower_only and is_zimo and self.last_draw_context == "REPLACEMENT"
        is_haidi = not flower_only and is_zimo and self.last_draw_context == "NORMAL" and self.last_draw_is_haidi
        is_hedi = bool(
            not flower_only
            and not is_zimo
            and (
                action.get("is_hedi")
                or (self.last_discard and self.last_discard.get("is_hedi"))
            )
        )
        is_tian_hu = bool(
            not flower_only
            and is_zimo
            and player.idx == self.dealer_idx
            and self.total_discard_count == 0
            and not self.opening_claim_occurred
        )
        is_di_hu = bool(
            not flower_only
            and is_zimo
            and player.idx != self.dealer_idx
            and self.normal_draw_counts.get(player.uid, 0) == 1
        )
        is_ren_hu = bool(
            not flower_only
            and not is_zimo
            and not is_qiang_gang
            and player.idx != self.dealer_idx
            and payer is not None
            and self.discard_counts.get(payer.uid, 0) == 1
            and self.total_discard_count <= 4
        )
        special_fans = {
            "qiang_gang": self.rules.qiang_gang_fan,
            "gang_shang": self.rules.gang_shang_fan,
            "haidi": self.rules.haidi_fan,
            "hedi": self.rules.hedi_fan,
            "tian_hu": self.rules.tian_hu_fan,
            "di_hu": self.rules.di_hu_fan,
            "ren_hu": self.rules.ren_hu_fan,
            "declared_ting": self.rules.declared_ting_fan,
            "di_ting": self.rules.di_ting_fan,
        }

        if flower_only:
            base_value = max(0, int(self.rules.base_score))
            result = {
                "base": base_value,
                "fan_total": 0,
                "total": base_value,
                "breakdown": [{"name": "底", "value": base_value}] if base_value else [],
            }
            if player.idx == self.dealer_idx:
                result["total"] += 1
                result["breakdown"].append({"name": "莊家", "value": 1})
            if self.lian_zhuang > 0:
                value = self.lian_zhuang * 2
                result["total"] += value
                result["breakdown"].append({"name": f"連莊 x{self.lian_zhuang}", "value": value})
        else:
            result = calculate_fan(
                player.hand,
                player.melds,
                tile,
                is_zimo,
                seat_wind=(player.idx - self.dealer_idx) % 4,
                round_wind=self.round_wind,
                flowers=[] if flower_win_type else player.flowers,
                is_dealer=player.idx == self.dealer_idx,
                lian_zhuang=self.lian_zhuang,
                is_qiang_gang=is_qiang_gang,
                is_gang_shang=is_gang_shang,
                is_haidi=is_haidi,
                is_hedi=is_hedi,
                is_tian_hu=is_tian_hu,
                is_di_hu=is_di_hu,
                is_ren_hu=is_ren_hu,
                is_declared_ting=player.declared_ting,
                is_di_ting=player.di_ting and player.di_ting_valid,
                special_fans=special_fans,
                base_score=self.rules.base_score,
            )

        normal_total = int(result["total"])
        if flower_win_type:
            flower_name = "八仙過海" if flower_win_type == "BAXIAN" else "七搶一"
            flower_value = int(self.rules.flower_win_fan)
            result["total"] += flower_value
            result["breakdown"].append({"name": flower_name, "value": flower_value})
        result["fan_total"] = max(0, int(result["total"]) - int(result["base"]))

        dealer_before = self.dealer_idx
        round_wind_before = self.round_wind
        lian_zhuang_before = self.lian_zhuang
        payer_uid = None if is_zimo else (payer.uid if payer else None)
        dealer_liability_bonus = 0
        if player.idx != self.dealer_idx:
            dealer_liability_bonus = 1 + max(0, self.lian_zhuang) * 2
        payment_units_by_uid = {}
        if flower_win_type == "QIQIANGYI" and flower_only:
            payer_uid = flower_payer_uid
            flower_payer = self.get_player(flower_payer_uid) if flower_payer_uid else None
            payment = int(result["total"])
            if flower_payer and flower_payer.idx == self.dealer_idx:
                payment += dealer_liability_bonus
            if flower_payer_uid:
                payment_units_by_uid[flower_payer_uid] = payment
        else:
            for candidate in self.players:
                if candidate.uid == player.uid:
                    continue
                payment = int(result["total"] if flower_win_type == "BAXIAN" else normal_total)
                if candidate.idx == self.dealer_idx:
                    payment += dealer_liability_bonus
                if flower_win_type == "QIQIANGYI" and candidate.uid == flower_payer_uid:
                    payment += int(self.rules.flower_win_fan)
                payment_units_by_uid[candidate.uid] = payment
        result["dealer_liability_bonus"] = dealer_liability_bonus
        score_deltas = settle_single_winner(
            [candidate.uid for candidate in self.players],
            player.uid,
            result["total"],
            is_zimo,
            payer_uid,
            payment_units_by_uid,
        )
        self._apply_score_deltas(score_deltas)
        dealer_message = self._update_dealer_after_round({player.idx})
        result["dealer_message"] = dealer_message

        self.state = STATE_END_GAME
        self.winner_uid = player.uid
        self.score_breakdown = result
        special_flags = {
            "qiang_gang": is_qiang_gang,
            "gang_shang": is_gang_shang,
            "haidi": is_haidi,
            "hedi": is_hedi,
            "tian_hu": is_tian_hu,
            "di_hu": is_di_hu,
            "ren_hu": is_ren_hu,
            "declared_ting": player.declared_ting,
            "di_ting": player.di_ting and player.di_ting_valid,
            "baxian": flower_win_type == "BAXIAN",
            "qiqiangyi": flower_win_type == "QIQIANGYI",
        }
        self._record_event(
            "HU",
            uid=player.uid,
            player_idx=player.idx,
            tile=tile,
            from_idx=from_idx,
            is_zimo=is_zimo,
            special_flags=special_flags,
        )
        match_ended = self._match_should_end()
        payload = {
            "winner_uid": player.uid,
            "winner_name": player.name,
            "winner_uids": [player.uid],
            "score_breakdown": result,
            "hand": player.hand,
            "melds": player.melds,
            "flowers": player.flowers,
            "winning_tile": tile,
            "from_idx": from_idx,
            "payer_uid": payer_uid,
            "flower_payer_uid": flower_payer_uid,
            "is_zimo": is_zimo,
            "special_flags": special_flags,
            "score_deltas": score_deltas,
            "payment_units_by_uid": payment_units_by_uid,
            "cumulative_scores": self._cumulative_scores(),
            "dealer_before": dealer_before,
            "dealer_after": self.dealer_idx,
            "round_wind_before": round_wind_before,
            "round_wind_after": self.round_wind,
            "lian_zhuang_before": lian_zhuang_before,
            "lian_zhuang_after": self.lian_zhuang,
            "match_ended": match_ended,
        }
        self._record_completed_hand(payload)
        if match_ended and self.match:
            self.match.finish({candidate.uid: candidate.idx for candidate in self.players})
        payload["match"] = self.match.to_snapshot() if self.match else {}
        payload["final_ranks"] = dict(self.match.final_ranks) if self.match else {}
        self.game_over_payload = payload
        return {"status": "HU", "payload": payload}

    def _finish_draw(self) -> Dict:
        dealer_before = self.dealer_idx
        round_wind_before = self.round_wind
        lian_zhuang_before = self.lian_zhuang
        score_deltas = {player.uid: 0 for player in self.players}
        dealer_message = self._update_dealer_after_round(set())
        self.state = STATE_END_GAME
        self.winner_uid = None
        self.score_breakdown = {
            "base": 0,
            "fan_total": 0,
            "total": 0,
            "breakdown": [],
            "dealer_message": dealer_message,
        }
        self._record_event("DRAW_GAME")
        match_ended = self._match_should_end()
        payload = {
            "winner_uid": None,
            "winner_name": "流局",
            "winner_uids": [],
            "score_breakdown": self.score_breakdown,
            "hand": [],
            "melds": [],
            "flowers": [],
            "winning_tile": None,
            "dealer_message": dealer_message,
            "score_deltas": score_deltas,
            "cumulative_scores": self._cumulative_scores(),
            "dealer_before": dealer_before,
            "dealer_after": self.dealer_idx,
            "round_wind_before": round_wind_before,
            "round_wind_after": self.round_wind,
            "lian_zhuang_before": lian_zhuang_before,
            "lian_zhuang_after": self.lian_zhuang,
            "match_ended": match_ended,
        }
        self._record_completed_hand(payload)
        if match_ended and self.match:
            self.match.finish({player.uid: player.idx for player in self.players})
        payload["match"] = self.match.to_snapshot() if self.match else {}
        payload["final_ranks"] = dict(self.match.final_ranks) if self.match else {}
        self.game_over_payload = payload
        return {"status": "DRAW_GAME", "payload": payload}

    def _apply_score_deltas(self, score_deltas: Dict[str, int]) -> None:
        if sum(int(value) for value in score_deltas.values()) != 0:
            raise ValueError("結算分數必須保持零和")
        for player in self.players:
            player.score += int(score_deltas.get(player.uid, 0))
        if self.match:
            self.match.apply_score_deltas(score_deltas)

    def _cumulative_scores(self) -> Dict[str, int]:
        return {player.uid: int(player.score) for player in self.players}

    def _record_completed_hand(self, payload: Dict) -> None:
        if not self.match:
            return
        self.match.record_hand(
            {
                **payload,
                "match_id": self.match.match_id,
                "hand_id": self.match.hand_id,
                "hand_number": self.match.hand_number,
                "events": list(self.event_log),
            }
        )

    def _match_should_end(self) -> bool:
        return bool(self.match and self.round_wind >= self.rules.match_winds)

    def _update_dealer_after_round(self, winner_indices: set[int]) -> str:
        if not winner_indices and self.rules.dealer_continues_on_draw:
            self.lian_zhuang += 1
            return "流局，莊家連莊"

        if self.dealer_idx in winner_indices:
            self.lian_zhuang += 1
            return "莊家胡牌，連莊"

        self.dealer_idx = (self.dealer_idx + 1) % 4
        self.lian_zhuang = 0
        if self.dealer_idx == 0:
            self.round_wind += 1
        if self._match_should_end():
            return "東風場結束"
        return "閒家胡牌，換莊"
