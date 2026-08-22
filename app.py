import base64
import binascii
import os
import random
import re
import string
import sys
import time
from typing import Dict, List, Optional

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

from backend.accounts import (
    AccountError,
    AccountManager,
    ONLINE_REWARD_COINS,
    ONLINE_REWARD_SECONDS,
    PLAY_ROUND_REWARD,
    RANKED_POINT_DELTAS,
)
from backend.game_engine import GameEngine, STATE_END_GAME, STATE_PLAYER_TURN, STATE_WAIT_RESPONSE
from backend.knowledge import build_visible_state
from backend.match_state import MATCH_ENDED
from backend.visible_ai import VisibleMahjongAI


CHAT_MAX_IMAGE_MB = 10
CHAT_MAX_IMAGE_BYTES = CHAT_MAX_IMAGE_MB * 1024 * 1024
CHAT_SOCKET_BUFFER_BYTES = 16 * 1024 * 1024

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "tw-mahjong-dev")
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=CHAT_SOCKET_BUFFER_BYTES)
account_manager = AccountManager()

rooms: Dict[str, Dict] = {}
rooms_by_uid: Dict[str, str] = {}
socket_accounts: Dict[str, Dict] = {}
matchmaking_queue: List[Dict] = []
online_sessions: Dict[str, Dict] = {}

DECISION_TIMEOUT_SECONDS = 50
OFFLINE_TIMEOUT_SECONDS = 150
AI_PROXY_DECISION_TIMEOUT_SECONDS = 120
AI_STEP_DELAY_SECONDS = 0.75
AI_MAX_STEPS_PER_TASK = 32
CHAT_MAX_MESSAGES = 80
CHAT_MAX_TEXT_LENGTH = 180
CHAT_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
CHAT_STICKERS = {
    "hu": "胡了",
    "zimo": "自摸",
    "nice": "好牌",
    "wait": "差一張",
    "again": "再一局",
    "thinking": "思考中",
}
DATA_URL_PATTERN = re.compile(r"^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$")
DEFAULT_BASE_STAKE = 10
QUEUE_MODES = ("matchmaking", "ranked")
ROOM_HAND_ENDED = "HAND_ENDED"
ROOM_MATCH_ENDED = "MATCH_ENDED"
ROOM_DISSOLVED = "DISSOLVED"
FORFEIT_SCORE_PENALTY = 3


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/test-clients")
@app.route("/quad")
def test_clients():
    return render_template("test_clients.html")


@app.route("/service-worker.js")
def service_worker():
    response = app.send_static_file("service-worker.js")
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@socketio.on("register_account")
def handle_register_account(data):
    try:
        account = account_manager.register((data or {}).get("username", ""), (data or {}).get("password", ""))
    except AccountError as exc:
        emit_error(str(exc))
        return

    authenticate_socket(account)
    emit_auth_success(account)


@socketio.on("login_account")
def handle_login_account(data):
    try:
        account = account_manager.login((data or {}).get("username", ""), (data or {}).get("password", ""))
    except AccountError as exc:
        emit_error(str(exc))
        return

    authenticate_socket(account)
    emit_auth_success(account)
    resume_active_room(account)


@socketio.on("resume_session")
def handle_resume_session(data):
    account = account_manager.resume((data or {}).get("token", ""))
    if not account:
        emit("auth_failed", {})
        return

    authenticate_socket(account)
    emit_auth_success(account)
    resume_active_room(account)


@socketio.on("create_room")
def handle_create_room(data):
    account = require_account()
    if not account:
        return

    active_room_id = rooms_by_uid.get(account["id"])
    if active_room_id and active_room_id in rooms and rooms[active_room_id]["state"] != "ENDED":
        rejoin_room(account, active_room_id)
        return

    remove_from_matchmaking(account["id"])
    track_stats = bool((data or {}).get("track_stats"))
    base_stake = parse_base_stake((data or {}).get("base_stake"))
    room_id = create_room_state(
        mode="custom",
        host_uid=account["id"],
        track_stats=track_stats,
        base_stake=base_stake,
    )

    join_room_logic(account, room_id)
    emit("room_created", room_public_info(room_id))


@socketio.on("join_room")
def handle_join_room(data):
    account = require_account()
    if not account:
        return

    room_id = (data or {}).get("room_id")
    if not room_id or room_id not in rooms:
        emit_error("Room not found")
        return

    room = rooms[room_id]
    already_in_room = any(player["uid"] == account["id"] for player in room["players"])
    if already_in_room:
        rejoin_room(account, room_id)
        return

    if room["state"] != "LOBBY":
        emit_error("Game already started")
        return

    if len(room["players"]) >= 4:
        emit_error("Room is full")
        return

    join_room_logic(account, room_id)


@socketio.on("disconnect")
def handle_disconnect(_reason=None):
    account = socket_accounts.pop(request.sid, None)
    if not account:
        return

    remove_from_matchmaking(account["id"])
    unregister_online_socket(account["id"], request.sid)
    emit_social_updates_to_online()

    room_id = rooms_by_uid.get(account["id"])
    if not room_id or room_id not in rooms:
        return

    # Reconnects can briefly leave more than one live socket for an account.
    # Start only this player's grace period after its final socket is gone.
    if account["id"] in online_account_ids():
        return

    schedule_player_offline(room_id, account["id"])


@socketio.on("set_ai_enabled")
def handle_set_ai_enabled(data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit_error("Join a room first")
        return

    enabled = bool((data or {}).get("enabled"))
    room_id = room["game"].room_id
    set_player_ai_enabled(room_id, account["id"], enabled)
    if room["state"] == ROOM_HAND_ENDED:
        auto_ready_uids = room.setdefault("auto_ready_uids", set())
        if enabled:
            auto_ready_ai_players(room_id)
        elif account["id"] in auto_ready_uids:
            auto_ready_uids.discard(account["id"])
            room.setdefault("next_round_votes", set()).discard(account["id"])
        emit_next_round_status(room_id)
        maybe_start_next_round_for_room(room_id)
    cancel_ai_for_room(room_id)
    socketio.emit("ai_status", {"enabled": enabled}, room=account["id"])
    emit_lobby_update(room_id)
    broadcast_game_state(room_id)
    maybe_run_ai(room_id)
    if room["state"] == "GAME":
        schedule_decision_timeout(room_id)


@socketio.on("action_discard")
def handle_discard(data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit_error("Join a room first")
        return

    game: GameEngine = room["game"]
    try:
        tile_index = int((data or {}).get("tile_index"))
        client_action_id = str((data or {}).get("client_action_id") or "")[:80]
        set_player_ai_enabled(game.room_id, account["id"], False)
        cancel_ai_for_room(game.room_id)
        discard_for_player(game.room_id, account["id"], tile_index, client_action_id)
    except (TypeError, ValueError) as exc:
        emit_error(str(exc))
        return


@socketio.on("action_reply")
def handle_action_reply(data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit_error("Join a room first")
        return

    game: GameEngine = room["game"]
    action_type = (data or {}).get("type") or "PASS"
    tile = (data or {}).get("tile")
    tiles = (data or {}).get("tiles")
    tile_index = (data or {}).get("tile_index")
    client_action_id = (data or {}).get("client_action_id")

    try:
        set_player_ai_enabled(game.room_id, account["id"], False)
        cancel_ai_for_room(game.room_id)
        if game.state == STATE_WAIT_RESPONSE:
            result = game.submit_response(account["id"], action_type, tile, tiles)
            handle_response_result(game.room_id, result)
            return

        if game.state == STATE_PLAYER_TURN and action_type in {"HU", "ANKANG", "BUKANG"}:
            result = game.apply_self_action(account["id"], action_type, tile)
            handle_action_result(game.room_id, result)
            return

        if game.state == STATE_PLAYER_TURN and action_type == "TING":
            discard_for_player(
                game.room_id,
                account["id"],
                int(tile_index),
                client_action_id=client_action_id,
                declare_ting=True,
            )
            return

        socketio.emit("clear_actions", room=account["id"])
    except (TypeError, ValueError) as exc:
        emit_error(str(exc))


@socketio.on("ask_hint")
def handle_ask_hint(_data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        return

    game: GameEngine = room["game"]
    player = game.get_player(account["id"])
    if not player:
        return
    if game.state != STATE_PLAYER_TURN or game.current_turn_idx != player.idx:
        return

    tile_index = VisibleMahjongAI.choose_discard(game, player.idx)
    emit("hint_reply", {"tile_index": tile_index})


@socketio.on("request_next_round")
def handle_request_next_round(_data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit_error("Join a room first")
        return

    room_id = room["game"].room_id
    if room["state"] != ROOM_HAND_ENDED:
        emit_error("Round has not ended")
        return

    votes = room.setdefault("next_round_votes", set())
    votes.add(account["id"])
    room.setdefault("auto_ready_uids", set()).discard(account["id"])
    emit_next_round_status(room_id)
    maybe_start_next_round_for_room(room_id)


@socketio.on("request_rematch")
def handle_request_rematch(_data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit_error("Join a room first")
        return

    room_id = room["game"].room_id
    if room["state"] != ROOM_MATCH_ENDED:
        emit_error("Match has not ended")
        return
    if room.get("mode") == "ranked":
        emit_error("Ranked matches must return to matchmaking")
        return
    if room.get("abandoned"):
        emit_error("This room has already been dissolved")
        return

    votes = room.setdefault("rematch_votes", set())
    votes.add(account["id"])
    emit_rematch_status(room_id)
    maybe_start_rematch_for_room(room_id)


@socketio.on("leave_room")
def handle_leave_room(_data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit("room_left", {"room_id": None})
        return

    dissolve_room_for_exit(room["game"].room_id, account["id"], "PLAYER_LEFT")


@socketio.on("dissolve_room")
def handle_dissolve_room(_data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit_error("Join a room first")
        return
    if room.get("mode") != "custom" or room.get("host_uid") != account["id"]:
        emit_error("Only the custom-room host can dissolve this room")
        return

    dissolve_room_for_exit(room["game"].room_id, account["id"], "HOST_DISSOLVED")


@socketio.on("join_matchmaking")
def handle_join_matchmaking(_data):
    account = require_account()
    if not account:
        return

    join_matchmaking_queue(account, "matchmaking")


@socketio.on("join_ranked")
def handle_join_ranked(_data):
    account = require_account()
    if not account:
        return

    join_matchmaking_queue(account, "ranked")


@socketio.on("leave_matchmaking")
def handle_leave_matchmaking(_data):
    account = require_account()
    if not account:
        return

    remove_from_matchmaking(account["id"], "matchmaking")
    emit("matchmaking_status", {"queued": False, "queue_count": queue_count("matchmaking"), "needed": 4, "mode": "matchmaking"})


@socketio.on("leave_ranked")
def handle_leave_ranked(_data):
    account = require_account()
    if not account:
        return

    remove_from_matchmaking(account["id"], "ranked")
    emit("ranked_status", {"queued": False, "queue_count": queue_count("ranked"), "needed": 4, "mode": "ranked"})


@socketio.on("request_history")
def handle_request_history(data):
    account = require_account()
    if not account:
        return

    username = str((data or {}).get("username") or "").strip()
    if username:
        try:
            profile = account_manager.get_public_profile_by_username(username)
        except AccountError as exc:
            emit_error(str(exc))
            return
        if not profile:
            emit_error("Player not found")
            return
    else:
        profile = account_manager.get_profile(account["id"])

    history = account_manager.get_recent_history(profile["account_id"]) if profile else []
    emit("history_result", {"profile": profile, "history": history})


@socketio.on("claim_daily_checkin")
def handle_claim_daily_checkin(_data):
    account = require_account()
    if not account:
        return

    result = account_manager.claim_daily_checkin(account["id"])
    emit_reward_state(account["id"])
    emit_profile_update(account["id"])
    if result["claimed"]:
        emit("reward_claimed", {"reason": "daily_checkin", "coins": result["reward"]})
    else:
        emit_error("今日已簽到")


@socketio.on("online_reward_ping")
def handle_online_reward_ping(_data):
    account = require_account()
    if not account:
        return

    reward = grant_due_online_reward(account["id"])
    emit_reward_state(account["id"])
    if reward:
        emit_profile_update(account["id"])
        emit("reward_claimed", {"reason": "online", "coins": reward})


@socketio.on("update_display_name")
def handle_update_display_name(data):
    account = require_account()
    if not account:
        return

    try:
        result = account_manager.update_display_name(account["id"], (data or {}).get("display_name", ""))
    except AccountError as exc:
        emit_error(str(exc))
        return

    socketio.emit("profile_update", result["profile"], room=account["id"])
    emit_reward_state(account["id"])
    if result["task"].get("claimed"):
        emit("reward_claimed", {"reason": result["task"]["key"], "coins": result["task"]["reward"]})


@socketio.on("update_character")
def handle_update_character(data):
    account = require_account()
    if not account:
        return

    try:
        profile = account_manager.update_character(account["id"], (data or {}).get("character_id", ""))
    except AccountError as exc:
        emit_error(str(exc))
        return

    socketio.emit("profile_update", profile, room=account["id"])
    sync_player_appearance(account["id"], profile)


@socketio.on("update_theme")
def handle_update_theme(data):
    account = require_account()
    if not account:
        return

    try:
        profile = account_manager.update_theme(account["id"], (data or {}).get("theme_id", ""))
    except AccountError as exc:
        emit_error(str(exc))
        return

    socketio.emit("profile_update", profile, room=account["id"])
    sync_player_appearance(account["id"], profile)


@socketio.on("request_social")
def handle_request_social(_data):
    account = require_account()
    if not account:
        return
    emit_social_state(account["id"])


@socketio.on("send_friend_request")
def handle_send_friend_request(data):
    account = require_account()
    if not account:
        return

    try:
        result = account_manager.send_friend_request(account["id"], (data or {}).get("username", ""))
    except AccountError as exc:
        emit_error(str(exc))
        return

    emit_social_state(account["id"])
    emit_social_state(result["target_id"])
    emit("social_notice", {"status": result["status"]})


@socketio.on("respond_friend_request")
def handle_respond_friend_request(data):
    account = require_account()
    if not account:
        return

    requester_id = str((data or {}).get("requester_id") or "")
    try:
        result = account_manager.respond_friend_request(account["id"], requester_id, bool((data or {}).get("accept")))
    except AccountError as exc:
        emit_error(str(exc))
        return

    emit_social_state(account["id"])
    emit_social_state(result["target_id"])
    emit("social_notice", {"status": result["status"]})


@socketio.on("remove_friend")
def handle_remove_friend(data):
    account = require_account()
    if not account:
        return

    friend_id = str((data or {}).get("friend_id") or "")
    try:
        result = account_manager.remove_friend(account["id"], friend_id)
    except AccountError as exc:
        emit_error(str(exc))
        return

    emit_social_state(account["id"])
    emit_social_state(result["target_id"])
    emit("social_notice", {"status": result["status"]})


@socketio.on("chat_send")
def handle_chat_send(data):
    account = require_account()
    if not account:
        return

    room = get_room_by_uid(account["id"])
    if not room:
        emit_error("Join a room first")
        return

    try:
        message = build_chat_message(room, account, data if isinstance(data, dict) else {})
    except ValueError as exc:
        emit_error(str(exc))
        return

    messages = room.setdefault("chat_messages", [])
    messages.append(message)
    if len(messages) > CHAT_MAX_MESSAGES:
        del messages[:-CHAT_MAX_MESSAGES]

    socketio.emit("chat_message", message, room=room["game"].room_id)


def authenticate_socket(account: Dict) -> None:
    socket_accounts[request.sid] = account
    join_room(account["id"])
    register_online_socket(account["id"], request.sid)
    emit_social_updates_to_online()


def emit_auth_success(account: Dict) -> None:
    profile = account_manager.get_profile(account["id"])
    emit(
        "auth_success",
        {
            "account": {"id": account["id"], "username": account["username"]},
            "profile": profile,
            "rewards": reward_state_payload(account["id"]),
            "social": account_manager.get_social_summary(account["id"], online_account_ids()),
            "token": account["token"],
            "active_room_id": account.get("active_room_id"),
        },
    )


def require_account() -> Optional[Dict]:
    account = socket_accounts.get(request.sid)
    if not account:
        emit_error("Please login first")
    return account


def register_online_socket(uid: str, sid: str) -> None:
    now = time.time()
    session = online_sessions.setdefault(uid, {"sids": set(), "last_reward_at": now})
    session["sids"].add(sid)


def unregister_online_socket(uid: str, sid: str) -> None:
    session = online_sessions.get(uid)
    if not session:
        return
    session["sids"].discard(sid)
    if not session["sids"]:
        online_sessions.pop(uid, None)


def online_account_ids() -> set[str]:
    return {uid for uid, session in online_sessions.items() if session.get("sids")}


def grant_due_online_reward(uid: str) -> int:
    now = time.time()
    session = online_sessions.setdefault(uid, {"sids": {request.sid}, "last_reward_at": now})
    elapsed = max(0, now - float(session.get("last_reward_at", now)))
    intervals = int(elapsed // ONLINE_REWARD_SECONDS)
    if intervals <= 0:
        return 0

    coins = intervals * ONLINE_REWARD_COINS
    session["last_reward_at"] = float(session.get("last_reward_at", now)) + intervals * ONLINE_REWARD_SECONDS
    account_manager.add_coins(uid, coins, "online_reward")
    return coins


def emit_profile_update(uid: str) -> None:
    profile = account_manager.get_profile(uid)
    if profile:
        socketio.emit("profile_update", profile, room=uid)


def sync_player_appearance(uid: str, profile: Optional[Dict] = None, notify: bool = True) -> None:
    room_id = rooms_by_uid.get(uid)
    if not room_id or room_id not in rooms:
        return

    profile = profile or account_manager.get_profile(uid) or {}
    room = rooms[room_id]
    player = next((item for item in room["players"] if item["uid"] == uid), None)
    if not player:
        return

    player["theme_id"] = profile.get("theme_id") or "classic"
    player["character_id"] = profile.get("character_id") or "default"
    if not notify:
        return

    emit_lobby_update(room_id)
    if room["state"] != "LOBBY":
        broadcast_game_state(room_id)


def emit_reward_state(uid: str) -> None:
    socketio.emit("reward_state", reward_state_payload(uid), room=uid)


def reward_state_payload(uid: str) -> Dict:
    session = online_sessions.get(uid)
    next_seconds = ONLINE_REWARD_SECONDS
    if session:
        next_seconds = max(0, int(ONLINE_REWARD_SECONDS - (time.time() - float(session.get("last_reward_at", time.time())))))
    return {
        **account_manager.get_reward_summary(uid),
        "online_next_seconds": next_seconds,
    }


def emit_social_state(uid: str) -> None:
    if uid in online_account_ids() or uid in rooms_by_uid:
        socketio.emit("social_state", account_manager.get_social_summary(uid, online_account_ids()), room=uid)


def emit_social_updates_to_online() -> None:
    for uid in list(online_account_ids()):
        emit_social_state(uid)


def resume_active_room(account: Dict) -> None:
    room_id = rooms_by_uid.get(account["id"]) or account.get("active_room_id")
    if not room_id or room_id not in rooms:
        if room_id:
            account_manager.set_active_room(account["id"], None)
        return

    rejoin_room(account, room_id)


def rejoin_room(account: Dict, room_id: str) -> None:
    join_room(room_id)
    rooms_by_uid[account["id"]] = room_id
    account_manager.set_active_room(account["id"], room_id)
    set_player_connected(room_id, account["id"], True)
    cancel_ai_for_room(room_id)
    sync_player_appearance(account["id"], notify=False)

    room = rooms[room_id]
    emit("rejoin_success", {"room_id": room_id, "state": room["state"]})
    emit_lobby_update(room_id)
    emit_chat_history(room_id, account["id"])
    if room["state"] == "GAME":
        emit("game_start", {"room_id": room_id})
        broadcast_game_state(room_id)
        game: GameEngine = room["game"]
        if game.state == STATE_WAIT_RESPONSE:
            responded_uids = {response["uid"] for response in game.response_queue}
            pending_actions = game.pending_actions_by_uid.get(account["id"], [])
            if account["id"] not in responded_uids and pending_actions:
                socketio.emit("request_action", {"actions": pending_actions}, room=account["id"])
        else:
            emit_self_actions(room_id)
    elif room["state"] in {ROOM_HAND_ENDED, ROOM_MATCH_ENDED}:
        broadcast_game_state(room_id)
        if room["game"].game_over_payload:
            socketio.emit("game_over", room["game"].game_over_payload, room=account["id"])
        if room["state"] == ROOM_HAND_ENDED:
            emit_next_round_status(room_id)
        else:
            socketio.emit("match_over", build_match_over_payload(room_id), room=account["id"])
            emit_rematch_status(room_id)


def generate_room_id() -> str:
    while True:
        room_id = "".join(random.choices(string.digits, k=6))
        if room_id not in rooms:
            return room_id


def create_room_state(mode: str, host_uid: Optional[str], track_stats: bool, base_stake: int) -> str:
    room_id = generate_room_id()
    game = GameEngine()
    game.room_id = room_id
    rooms[room_id] = {
        "game": game,
        "players": [],
        "state": "LOBBY",
        "mode": mode,
        "host_uid": host_uid,
        "track_stats": bool(track_stats),
        "base_stake": base_stake,
        "round_started_at": None,
        "pending_actions_by_uid": {},
        "ai_busy": False,
        "ai_control_seq": 0,
        "decision_seq": 0,
        "decision_deadline": None,
        "decision_timeout_seconds": None,
        "state_seq": 0,
        "next_round_votes": set(),
        "auto_ready_uids": set(),
        "rematch_votes": set(),
        "chat_messages": [],
        "current_match_id": None,
        "current_hand_id": None,
        "match_started_at": None,
        "round_result_recorded": False,
        "round_result_recording": False,
        "match_result_recorded": False,
        "match_result_recording": False,
        "play_reward_recorded": False,
        "round_started_dealer_info": None,
        "abandoned": False,
        "forfeit_uid": None,
    }
    return room_id


def make_match_id(room_id: str, started_at: Optional[float] = None) -> str:
    stamp = int((started_at or time.time()) * 1000)
    return f"match_{room_id}_{stamp}_{random.randrange(100000)}"


def prepare_match_tracking(room_id: str) -> None:
    room = rooms[room_id]
    game: GameEngine = room["game"]
    started_at = time.time()
    room["match_started_at"] = started_at
    room["current_match_id"] = game.match.match_id if game.match else make_match_id(room_id, started_at)
    room["match_result_recorded"] = False
    room["match_result_recording"] = False
    room["play_reward_recorded"] = False


def prepare_round_tracking(room_id: str) -> None:
    room = rooms[room_id]
    game: GameEngine = room["game"]
    started_at = time.time()
    room["round_started_at"] = started_at
    room["current_hand_id"] = game.match.hand_id if game.match else None
    room["round_result_recorded"] = False
    room["round_result_recording"] = False
    room["round_started_dealer_info"] = {
        "dealer_idx": game.dealer_idx,
        "round_wind": game.round_wind,
        "lian_zhuang": game.lian_zhuang,
    }


def parse_base_stake(value) -> int:
    try:
        stake = int(value)
    except (TypeError, ValueError):
        stake = DEFAULT_BASE_STAKE
    return max(1, min(999, stake))


def room_public_info(room_id: str) -> Dict:
    room = rooms[room_id]
    game: GameEngine = room["game"]
    return {
        "room_id": room_id,
        "mode": room.get("mode", "custom"),
        "track_stats": bool(room.get("track_stats")),
        "base_stake": int(room.get("base_stake", DEFAULT_BASE_STAKE)),
        "host_uid": room.get("host_uid"),
        "ruleset_id": game.rules.id,
        "ruleset_name": game.rules.display_name,
        "rules": game.rules.to_public_json(),
    }


def join_matchmaking_queue(account: Dict, queue_mode: str) -> None:
    if queue_mode not in QUEUE_MODES:
        emit_error("Unknown queue")
        return

    active_room_id = rooms_by_uid.get(account["id"])
    if active_room_id and active_room_id in rooms and rooms[active_room_id]["state"] != "ENDED":
        rejoin_room(account, active_room_id)
        return

    remove_from_matchmaking(account["id"])
    profile = account_manager.get_profile(account["id"]) or {}
    matchmaking_queue.append(
        {
            "account": account,
            "sid": request.sid,
            "joined_at": time.time(),
            "mode": queue_mode,
            "rank_level": profile.get("rank_level", 1),
            "rank_name": profile.get("rank_name", "一段"),
            "rank_points": profile.get("rank_points", 0),
        }
    )
    emit_matchmaking_status(queue_mode)
    maybe_start_matched_room(queue_mode)


def queue_count(queue_mode: str) -> int:
    return sum(1 for item in matchmaking_queue if item.get("mode", "matchmaking") == queue_mode)


def queue_event_name(queue_mode: str) -> str:
    return "ranked_status" if queue_mode == "ranked" else "matchmaking_status"


def remove_from_matchmaking(uid: str, queue_mode: Optional[str] = None) -> None:
    removed_modes = {
        item.get("mode", "matchmaking")
        for item in matchmaking_queue
        if item["account"]["id"] == uid and (queue_mode is None or item.get("mode", "matchmaking") == queue_mode)
    }
    if not removed_modes:
        return

    matchmaking_queue[:] = [
        item
        for item in matchmaking_queue
        if not (item["account"]["id"] == uid and (queue_mode is None or item.get("mode", "matchmaking") == queue_mode))
    ]
    for mode in removed_modes:
        emit_matchmaking_status(mode)


def emit_matchmaking_status(queue_mode: Optional[str] = None) -> None:
    modes = [queue_mode] if queue_mode else list(QUEUE_MODES)
    for mode in modes:
        items = [item for item in matchmaking_queue if item.get("mode", "matchmaking") == mode]
        queued_uids = {item["account"]["id"] for item in items}
        for item in items:
            payload = {
                "queued": item["account"]["id"] in queued_uids,
                "queue_count": len(items),
                "needed": 4,
                "mode": mode,
            }
            if mode == "ranked":
                payload.update(
                    {
                        "rank_level": item.get("rank_level", 1),
                        "rank_name": item.get("rank_name", "一段"),
                        "rank_points": item.get("rank_points", 0),
                    }
                )
            socketio.emit(
                queue_event_name(mode),
                payload,
                room=item["account"]["id"],
            )


def maybe_start_matched_room(queue_mode: Optional[str] = None) -> None:
    modes = [queue_mode] if queue_mode else list(QUEUE_MODES)
    for mode in modes:
        while queue_count(mode) >= 4:
            batch = [item for item in matchmaking_queue if item.get("mode", "matchmaking") == mode][:4]
            for item in batch:
                matchmaking_queue.remove(item)
            room_mode = "ranked" if mode == "ranked" else "matchmaking"
            room_id = create_room_state(
                mode=room_mode,
                host_uid=None,
                track_stats=True,
                base_stake=DEFAULT_BASE_STAKE,
            )
            for item in batch:
                join_room_logic(item["account"], room_id, sid=item["sid"])
            socketio.emit("match_found", room_public_info(room_id), room=room_id)
            start_game_for_room(room_id)
        emit_matchmaking_status(mode)


def emit_error(message: str, uid: Optional[str] = None) -> None:
    payload = {"message": message}
    if uid:
        socketio.emit("server_error", payload, room=uid)
    else:
        emit("server_error", payload)


def get_room_by_uid(uid: str) -> Optional[Dict]:
    room_id = rooms_by_uid.get(uid)
    if not room_id:
        return None
    return rooms.get(room_id)


def join_room_logic(account: Dict, room_id: str, sid: Optional[str] = None) -> None:
    room = rooms[room_id]
    game: GameEngine = room["game"]
    profile = account_manager.get_profile(account["id"]) or {}

    if sid:
        socketio.server.enter_room(sid, room_id, namespace="/")
        socketio.server.enter_room(sid, account["id"], namespace="/")
    else:
        join_room(room_id)
        join_room(account["id"])
    if game.add_player(account["id"], account["username"]):
        room["players"].append(
            {
                "uid": account["id"],
                "name": account["username"],
                "connected": True,
                "ai_enabled": False,
                "auto_play_enabled": False,
                "connection_seq": 0,
                "offline_deadline": None,
                "rank_level": profile.get("rank_level", 1),
                "rank_name": profile.get("rank_name", "一段"),
                "rank_points": profile.get("rank_points", 0),
                "theme_id": profile.get("theme_id", "classic"),
                "character_id": profile.get("character_id", "default"),
            }
        )
    rooms_by_uid[account["id"]] = room_id
    account_manager.set_active_room(account["id"], room_id)

    if sid:
        socketio.emit("join_success", {"room_id": room_id, "uid": account["id"]}, room=account["id"])
    else:
        emit("join_success", {"room_id": room_id, "uid": account["id"]})
    emit_lobby_update(room_id)
    emit_chat_history(room_id, account["id"])

    if room["state"] == "LOBBY" and len(room["players"]) == 4:
        start_game_for_room(room_id)


def emit_lobby_update(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    info = room_public_info(room_id)
    socketio.emit(
        "update_lobby",
        {
            "room_id": room_id,
            "mode": info["mode"],
            "track_stats": info["track_stats"],
            "base_stake": info["base_stake"],
            "host_uid": info["host_uid"],
            "ruleset_id": info["ruleset_id"],
            "ruleset_name": info["ruleset_name"],
            "rules": info["rules"],
            "players": room["players"],
            "count": len(room["players"]),
            "total": 4,
            "state": room["state"],
        },
        room=room_id,
    )


def emit_chat_history(room_id: str, uid: str) -> None:
    if room_id not in rooms:
        return
    socketio.emit("chat_history", {"messages": rooms[room_id].get("chat_messages", [])}, room=uid)


def build_chat_message(room: Dict, account: Dict, data: Dict) -> Dict:
    kind = str(data.get("kind") or "text").strip().lower()
    message = {
        "id": f"chat_{int(time.time() * 1000)}_{random.randrange(100000)}",
        "kind": kind,
        "sender_uid": account["id"],
        "sender_name": account["username"],
        "created_at": int(time.time()),
    }

    if kind == "text":
        text = str(data.get("text") or "").strip()
        if not text:
            raise ValueError("Message is empty")
        message["text"] = text[:CHAT_MAX_TEXT_LENGTH]
        return message

    if kind == "sticker":
        sticker_id = str(data.get("sticker_id") or "").strip()
        if sticker_id not in CHAT_STICKERS:
            raise ValueError("Sticker is not available")
        message["sticker_id"] = sticker_id
        message["sticker_label"] = CHAT_STICKERS[sticker_id]
        return message

    if kind == "image":
        image = validate_chat_image(data.get("image") or {})
        message["image"] = image
        caption = str(data.get("caption") or "").strip()
        if caption:
            message["caption"] = caption[:CHAT_MAX_TEXT_LENGTH]
        return message

    raise ValueError("Unsupported chat message")


def validate_chat_image(image: Dict) -> Dict:
    if not isinstance(image, dict):
        raise ValueError("Image data is invalid")

    data_url = str(image.get("data_url") or "")
    match = DATA_URL_PATTERN.match(data_url)
    if not match:
        raise ValueError("Only PNG, JPEG, GIF, or WebP images are supported")

    mime_type, raw_base64 = match.groups()
    if mime_type not in CHAT_ALLOWED_IMAGE_TYPES:
        raise ValueError("Image type is not supported")

    compact_base64 = re.sub(r"\s+", "", raw_base64)
    try:
        decoded = base64.b64decode(compact_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("Image data is invalid") from exc

    if len(decoded) > CHAT_MAX_IMAGE_BYTES:
        raise ValueError(f"Image must be {CHAT_MAX_IMAGE_MB} MB or smaller")

    name = str(image.get("name") or "image").strip()[:80]
    return {
        "data_url": f"data:{mime_type};base64,{compact_base64}",
        "mime_type": mime_type,
        "size": len(decoded),
        "name": name or "image",
    }


def emit_next_round_status(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    votes = room.get("next_round_votes", set())
    socketio.emit(
        "next_round_status",
        {
            "ready_count": len(votes),
            "total": len(room["players"]),
            "ready_uids": list(votes),
        },
        room=room_id,
    )


def auto_ready_ai_players(room_id: str) -> None:
    if room_id not in rooms or rooms[room_id]["state"] != ROOM_HAND_ENDED:
        return

    votes = rooms[room_id].setdefault("next_round_votes", set())
    auto_ready_uids = rooms[room_id].setdefault("auto_ready_uids", set())
    for player in rooms[room_id]["players"]:
        if is_ai_controlled(room_id, player["uid"]):
            if player["uid"] not in votes:
                auto_ready_uids.add(player["uid"])
            votes.add(player["uid"])


def maybe_start_next_round_for_room(room_id: str) -> None:
    if room_id not in rooms or rooms[room_id]["state"] != ROOM_HAND_ENDED:
        return

    room = rooms[room_id]
    player_uids = {player["uid"] for player in room["players"]}
    votes = room.setdefault("next_round_votes", set())
    if len(player_uids) == 4 and player_uids.issubset(votes):
        start_next_round_for_room(room_id)


def emit_rematch_status(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    votes = room.get("rematch_votes", set())
    socketio.emit(
        "rematch_status",
        {
            "ready_count": len(votes),
            "total": len(room["players"]),
            "ready_uids": list(votes),
            "can_rematch": room["state"] == ROOM_MATCH_ENDED
            and room.get("mode") != "ranked"
            and not room.get("abandoned"),
        },
        room=room_id,
    )


def maybe_start_rematch_for_room(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    if room["state"] != ROOM_MATCH_ENDED or room.get("mode") == "ranked" or room.get("abandoned"):
        return
    player_uids = {player["uid"] for player in room["players"]}
    votes = room.setdefault("rematch_votes", set())
    if len(player_uids) == 4 and player_uids.issubset(votes):
        start_rematch_for_room(room_id)


def start_game_for_room(room_id: str) -> None:
    room = rooms[room_id]
    if room["state"] != "LOBBY":
        return
    if len(room["players"]) != 4:
        socketio.emit("server_error", {"message": "Need 4 players to start"}, room=room_id)
        return

    game: GameEngine = room["game"]
    room["state"] = "GAME"
    room["next_round_votes"] = set()
    room["auto_ready_uids"] = set()
    game.start_game()
    prepare_match_tracking(room_id)
    prepare_round_tracking(room_id)

    socketio.emit("game_start", {"room_id": room_id}, room=room_id)
    flower_result = game.resolve_pending_flower_win(game.current_turn_idx)
    if flower_result:
        handle_action_result(room_id, flower_result)
        return
    broadcast_game_state(room_id)
    emit_self_actions(room_id)
    maybe_run_ai(room_id)
    schedule_decision_timeout(room_id)


def start_rematch_for_room(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    if room["state"] != ROOM_MATCH_ENDED or room.get("mode") == "ranked" or room.get("abandoned"):
        return
    if len(room["players"]) != 4:
        socketio.emit("server_error", {"message": "Need 4 players for a rematch"}, room=room_id)
        return

    game: GameEngine = room["game"]
    room["state"] = "GAME"
    room["pending_actions_by_uid"] = {}
    room["ai_busy"] = False
    room["ai_control_seq"] = int(room.get("ai_control_seq", 0)) + 1
    room["decision_seq"] = int(room.get("decision_seq", 0)) + 1
    room["decision_deadline"] = None
    room["decision_timeout_seconds"] = None
    room["next_round_votes"] = set()
    room["auto_ready_uids"] = set()
    room["rematch_votes"] = set()
    room["chat_messages"] = []
    room["match_summary"] = None
    room["abandoned"] = False
    room["forfeit_uid"] = None

    for player in room["players"]:
        player["ai_enabled"] = False
        player["auto_play_enabled"] = False
        account_manager.set_active_room(player["uid"], room_id)
        socketio.emit("ai_status", {"enabled": False}, room=player["uid"])

    game.start_game()
    prepare_match_tracking(room_id)
    prepare_round_tracking(room_id)

    socketio.emit("rematch_started", {"room_id": room_id}, room=room_id)
    socketio.emit("game_start", {"room_id": room_id}, room=room_id)
    flower_result = game.resolve_pending_flower_win(game.current_turn_idx)
    if flower_result:
        handle_action_result(room_id, flower_result)
        return
    broadcast_game_state(room_id)
    emit_self_actions(room_id)
    maybe_run_ai(room_id)
    schedule_decision_timeout(room_id)


def start_next_round_for_room(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    if room["state"] != ROOM_HAND_ENDED:
        return
    if len(room["players"]) != 4:
        socketio.emit("server_error", {"message": "Need 4 players to continue"}, room=room_id)
        return

    game: GameEngine = room["game"]
    room["state"] = "GAME"
    room["pending_actions_by_uid"] = {}
    room["ai_busy"] = False
    room["ai_control_seq"] = int(room.get("ai_control_seq", 0)) + 1
    room["decision_seq"] = int(room.get("decision_seq", 0)) + 1
    room["decision_deadline"] = None
    room["decision_timeout_seconds"] = None
    room["next_round_votes"] = set()
    room["auto_ready_uids"] = set()
    game.start_round()
    prepare_round_tracking(room_id)

    for player in room["players"]:
        account_manager.set_active_room(player["uid"], room_id)

    socketio.emit("next_round_started", {"room_id": room_id}, room=room_id)
    socketio.emit("game_start", {"room_id": room_id}, room=room_id)
    flower_result = game.resolve_pending_flower_win(game.current_turn_idx)
    if flower_result:
        handle_action_result(room_id, flower_result)
        return
    broadcast_game_state(room_id)
    emit_self_actions(room_id)
    maybe_run_ai(room_id)
    schedule_decision_timeout(room_id)


def handle_response_result(room_id: str, result: Dict) -> None:
    if result["status"] == "IGNORED":
        return

    if result["status"] == "WAITING":
        broadcast_game_state(room_id)
        maybe_run_ai(room_id)
        return

    if result["status"] == "PASS":
        clear_pending_actions(room_id)
        advance_after_passes(room_id)
        return

    if result["status"] == "SELF_ACTION":
        clear_pending_actions(room_id)
        handle_action_result(room_id, result)
        return

    if result["status"] == "ACTION":
        game: GameEngine = rooms[room_id]["game"]
        clear_pending_actions(room_id)
        action_result = game.apply_claim_action(result["action"])
        handle_action_result(room_id, action_result)


def handle_action_result(room_id: str, result: Dict) -> None:
    forced_result = maybe_apply_declared_ting_bukang(room_id, result)
    if forced_result:
        handle_action_result(room_id, forced_result)
        return

    broadcast_game_state(room_id)

    if result["status"] == "WAIT_RESPONSE":
        emit_response_requests(room_id, result.get("actions", []))
        maybe_run_ai(room_id)
        schedule_decision_timeout(room_id)
        return

    if result["status"] in {"HU", "DRAW_GAME"}:
        room = rooms[room_id]
        payload = result["payload"]
        match_ended = bool(payload.get("match_ended"))
        room["state"] = ROOM_MATCH_ENDED if match_ended else ROOM_HAND_ENDED
        room["next_round_votes"] = set()
        room["auto_ready_uids"] = set()
        room["rematch_votes"] = set()
        clear_decision_timeout(room_id)
        record_round_result(room_id, payload)
        socketio.emit("game_over", payload, room=room_id)
        if match_ended:
            finalize_match_result(room_id)
            grant_play_rewards(room_id)
            for player in room["players"]:
                profile = account_manager.get_profile(player["uid"])
                if profile:
                    socketio.emit("profile_update", profile, room=player["uid"])
            socketio.emit("match_over", build_match_over_payload(room_id), room=room_id)
            emit_rematch_status(room_id)
        else:
            auto_ready_ai_players(room_id)
            emit_next_round_status(room_id)
            maybe_start_next_round_for_room(room_id)
        return

    emit_self_actions(room_id)
    maybe_run_ai(room_id)
    schedule_decision_timeout(room_id)


def maybe_apply_declared_ting_bukang(room_id: str, result: Dict) -> Optional[Dict]:
    if result.get("status") in {"HU", "DRAW_GAME", "WAIT_RESPONSE"}:
        return None
    if room_id not in rooms:
        return None
    game: GameEngine = rooms[room_id]["game"]
    if game.state != STATE_PLAYER_TURN or not game.players:
        return None

    player = game.players[game.current_turn_idx]
    if not player.declared_ting:
        return None
    actions = game.check_self_actions()
    if any(action["type"] == "HU" for action in actions):
        return None
    bukang = next((action for action in actions if action["type"] == "BUKANG"), None)
    if not bukang:
        return None
    return game.apply_self_action(player.uid, "BUKANG", bukang.get("tile"))


def record_round_result(room_id: str, payload: Dict) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    game: GameEngine = room["game"]
    if room.get("round_result_recorded") or room.get("round_result_recording"):
        return

    room["round_result_recording"] = True
    if game.match and game.match.completed_hands:
        hand_result = game.match.completed_hands[-1]
        hand_result["played_at"] = int(time.time())
        hand_result["duration_seconds"] = max(
            0,
            int(time.time() - (room.get("round_started_at") or time.time())),
        )
        hand_result["room_id"] = room_id
        hand_result["mode"] = room.get("mode", "custom")
    room["round_result_recorded"] = True
    room["round_result_recording"] = False


def finalize_match_result(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    game: GameEngine = room["game"]
    match = game.match
    if not match or not match.ended or room.get("match_result_recorded") or room.get("match_result_recording"):
        return

    room["match_result_recording"] = True
    scores = dict(match.cumulative_scores)
    final_ranks = dict(match.final_ranks)
    top_uid = next((uid for uid, rank in final_ranks.items() if rank == 1), None)
    top_player = game.get_player(top_uid) if top_uid else None
    is_draw = len(set(scores.values())) <= 1
    rank_deltas = {player.uid: 0 for player in game.players}
    if room.get("mode") == "ranked" and not is_draw:
        rank_deltas = {
            player.uid: RANKED_POINT_DELTAS.get(final_ranks.get(player.uid, 4), 0)
            for player in game.players
        }

    players_summary = [
        {
            "uid": player.uid,
            "name": player.name,
            "idx": player.idx,
            "score_delta": int(scores.get(player.uid, 0)),
            "cumulative_score": int(scores.get(player.uid, 0)),
            "final_rank": int(final_ranks.get(player.uid, 4)),
            "rank_delta": int(rank_deltas.get(player.uid, 0)),
            "forfeited": player.uid == room.get("forfeit_uid"),
        }
        for player in game.players
    ]
    duration = max(0, int(time.time() - (room.get("match_started_at") or time.time())))
    hands = [dict(hand) for hand in match.completed_hands]
    base_stake = int(room.get("base_stake", DEFAULT_BASE_STAKE))
    round_detail = {
        "room_id": room_id,
        "mode": room.get("mode", "custom"),
        "ruleset_id": match.ruleset_id,
        "match_id": match.match_id,
        "duration_seconds": duration,
        "players": players_summary,
        "hands": hands,
        "hand_count": len(hands),
        "final_ranks": final_ranks,
        "cumulative_scores": scores,
        "abandoned": bool(room.get("abandoned")),
        "forfeit_uid": room.get("forfeit_uid"),
    }
    dealer_info = {
        "dealer_idx": game.dealer_idx,
        "round_wind": game.round_wind,
        "lian_zhuang": game.lian_zhuang,
        "message": "玩家退出，牌桌已解散" if room.get("abandoned") else "東風場結束",
    }
    records = []
    for player in game.players:
        score_delta = int(scores.get(player.uid, 0))
        final_rank = int(final_ranks.get(player.uid, 4))
        records.append(
            {
                "id": f"hist_{player.uid}_{match.match_id}",
                "account_id": player.uid,
                "username": player.name,
                "match_id": match.match_id,
                "played_at": int(match.ended_at or time.time()),
                "mode": room.get("mode", "custom"),
                "room_id": room_id,
                "players": players_summary,
                "winner_uid": top_uid,
                "winner_name": top_player.name if top_player else None,
                "is_draw": is_draw,
                "fan_total": 0,
                "fan_breakdown": [],
                "round_detail": round_detail,
                "score_delta": score_delta,
                "coin_delta": score_delta * base_stake,
                "final_rank": final_rank,
                "rank_delta": rank_deltas.get(player.uid, 0),
                "duration_seconds": duration,
                "dealer_info": dealer_info,
                "seat_idx": player.idx,
                "result": "DRAW" if is_draw else ("WIN" if final_rank == 1 else "LOSE"),
                "coin_reason": (
                    "ranked_forfeit"
                    if room.get("mode") == "ranked" and room.get("abandoned")
                    else ("ranked_match" if room.get("mode") == "ranked" else "match")
                ),
            }
        )

    try:
        if room.get("track_stats"):
            account_manager.record_match_results(records)
    except Exception:
        room["match_result_recording"] = False
        raise

    room["match_summary"] = {
        **round_detail,
        "winner_uid": top_uid,
        "winner_name": top_player.name if top_player else None,
        "rank_deltas": rank_deltas,
    }
    room["match_result_recorded"] = True
    room["match_result_recording"] = False


def build_match_over_payload(room_id: str) -> Dict:
    if room_id not in rooms:
        return {}
    room = rooms[room_id]
    game: GameEngine = room["game"]
    return dict(room.get("match_summary") or {
        "match_id": game.match.match_id if game.match else None,
        "ruleset_id": game.match.ruleset_id if game.match else None,
        "players": [],
        "hands": list(game.match.completed_hands) if game.match else [],
        "final_ranks": dict(game.match.final_ranks) if game.match else {},
        "cumulative_scores": dict(game.match.cumulative_scores) if game.match else {},
    }) | {
        "mode": room.get("mode", "custom"),
        "can_rematch": room["state"] == ROOM_MATCH_ENDED
        and room.get("mode") != "ranked"
        and not room.get("abandoned"),
        "abandoned": bool(room.get("abandoned")),
        "forfeit_uid": room.get("forfeit_uid"),
    }


def finalize_forfeit_match(room_id: str, leaver_uid: str) -> Dict:
    if room_id not in rooms:
        return {}

    room = rooms[room_id]
    game: GameEngine = room["game"]
    match = game.match
    if not match or match.ended or room["state"] not in {"GAME", ROOM_HAND_ENDED}:
        return {}
    if not game.get_player(leaver_uid):
        return {}

    remaining_uids = [player.uid for player in game.players if player.uid != leaver_uid]
    scores = {player.uid: int(match.cumulative_scores.get(player.uid, 0)) for player in game.players}
    forfeit_loss = max(FORFEIT_SCORE_PENALTY, scores[leaver_uid] + 1)
    base_share, extra = divmod(forfeit_loss, len(remaining_uids))
    for index, uid in enumerate(remaining_uids):
        scores[uid] += base_share + (1 if index < extra else 0)
    scores[leaver_uid] -= forfeit_loss

    seat_by_uid = {player.uid: player.idx for player in game.players}
    ranked_remaining = sorted(
        remaining_uids,
        key=lambda uid: (-scores[uid], seat_by_uid.get(uid, 99)),
    )
    match.cumulative_scores = scores
    match.final_ranks = {uid: rank for rank, uid in enumerate(ranked_remaining, start=1)}
    match.final_ranks[leaver_uid] = 4
    match.state = MATCH_ENDED
    match.ended_at = time.time()

    room["state"] = ROOM_MATCH_ENDED
    room["abandoned"] = True
    room["forfeit_uid"] = leaver_uid
    room["next_round_votes"] = set()
    room["auto_ready_uids"] = set()
    room["rematch_votes"] = set()
    clear_decision_timeout(room_id)
    cancel_ai_for_room(room_id)
    finalize_match_result(room_id)
    return build_match_over_payload(room_id)


def leave_account_sockets_from_room(uid: str, room_id: str) -> None:
    for sid in list(online_sessions.get(uid, {}).get("sids", set())):
        socketio.server.leave_room(sid, room_id, namespace="/")


def dissolve_room_for_exit(room_id: str, leaver_uid: str, reason: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    leaver = next((player for player in room["players"] if player["uid"] == leaver_uid), None)
    leaver_name = leaver.get("name") if leaver else leaver_uid
    was_active = room["state"] in {"GAME", ROOM_HAND_ENDED}
    match_payload = finalize_forfeit_match(room_id, leaver_uid) if was_active else {}
    room["state"] = ROOM_DISSOLVED
    cancel_ai_for_room(room_id)
    room["decision_seq"] = int(room.get("decision_seq", 0)) + 1
    room["decision_deadline"] = None

    payload = {
        "room_id": room_id,
        "reason": reason,
        "leaver_uid": leaver_uid,
        "leaver_name": leaver_name,
        "penalized": bool(match_payload),
        "mode": room.get("mode", "custom"),
        "match": match_payload,
    }
    player_uids = [player["uid"] for player in room["players"]]
    for uid in player_uids:
        account_manager.set_active_room(uid, None)
        if rooms_by_uid.get(uid) == room_id:
            rooms_by_uid.pop(uid, None)
        socketio.emit("room_dissolved", payload, room=uid)
        profile = account_manager.get_profile(uid)
        if profile:
            socketio.emit("profile_update", profile, room=uid)
        leave_account_sockets_from_room(uid, room_id)

    rooms.pop(room_id, None)


def grant_play_rewards(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    if room.get("play_reward_recorded"):
        return

    room["play_reward_recorded"] = True
    for player in room["players"]:
        account_manager.add_coins(player["uid"], PLAY_ROUND_REWARD, "play_round")
        socketio.emit("reward_claimed", {"reason": "play_round", "coins": PLAY_ROUND_REWARD}, room=player["uid"])
        emit_reward_state(player["uid"])


def advance_after_passes(room_id: str) -> None:
    game: GameEngine = rooms[room_id]["game"]
    result = game.advance_turn()
    handle_action_result(room_id, result)


def discard_for_player(
    room_id: str,
    uid: str,
    tile_index: int,
    client_action_id: Optional[str] = None,
    declare_ting: bool = False,
    automatic_reason: Optional[str] = None,
) -> None:
    game: GameEngine = rooms[room_id]["game"]
    actions = (
        game.declare_ting(uid, tile_index)
        if declare_ting
        else game.discard_tile(uid, tile_index)
    )
    emit_discard_event(room_id, uid, tile_index, client_action_id, automatic_reason)
    broadcast_game_state(room_id)
    if actions:
        emit_response_requests(room_id, actions)
        schedule_decision_timeout(room_id)
        maybe_run_ai(room_id)
        return

    advance_after_passes(room_id)


def emit_response_requests(room_id: str, actions: List[Dict]) -> None:
    actions_by_uid: Dict[str, List[Dict]] = {}
    for action in actions:
        actions_by_uid.setdefault(action["uid"], []).append(action)

    rooms[room_id]["pending_actions_by_uid"] = actions_by_uid
    for uid, player_actions in actions_by_uid.items():
        socketio.emit("request_action", {"actions": player_actions}, room=uid)


def emit_self_actions(room_id: str) -> None:
    game: GameEngine = rooms[room_id]["game"]
    if game.state != STATE_PLAYER_TURN:
        return

    actions = game.check_self_actions()
    uid = game.players[game.current_turn_idx].uid
    if actions:
        socketio.emit("request_action", {"actions": actions, "self": True}, room=uid)
    else:
        socketio.emit("clear_actions", room=uid)


def broadcast_game_state(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    game: GameEngine = room["game"]
    room["state_seq"] = int(room.get("state_seq", 0)) + 1
    state_seq = room["state_seq"]
    for player in game.players:
        socketio.emit("game_update", enriched_snapshot(room_id, player.uid, state_seq), room=player.uid)


def enriched_snapshot(room_id: str, recipient_uid: str, state_seq: Optional[int] = None) -> Dict:
    room = rooms[room_id]
    game: GameEngine = room["game"]
    snapshot = game.get_snapshot(recipient_uid)
    snapshot["room_state"] = room["state"]
    snapshot["state_seq"] = int(room.get("state_seq", 0) if state_seq is None else state_seq)
    snapshot["server_time"] = time.time()
    meta_by_uid = {player["uid"]: player for player in room["players"]}
    for player in snapshot["players"]:
        meta = meta_by_uid.get(player["uid"], {})
        player["connected"] = bool(meta.get("connected"))
        player["ai_enabled"] = bool(meta.get("ai_enabled"))
        player["auto_play_enabled"] = bool(meta.get("auto_play_enabled"))
        player["ai_controlled"] = bool(meta.get("ai_enabled") or meta.get("auto_play_enabled"))
        player["theme_id"] = meta.get("theme_id") or "classic"
        player["character_id"] = meta.get("character_id") or "default"
    snapshot["room_mode"] = room.get("mode", "custom")
    snapshot["host_uid"] = room.get("host_uid")
    snapshot["knowledge"] = build_visible_state(
        game,
        recipient_uid,
        available_actions=room.get("pending_actions_by_uid", {}).get(recipient_uid, []),
    )
    if room.get("decision_deadline"):
        snapshot["decision_deadline"] = room["decision_deadline"]
        snapshot["decision_timeout_seconds"] = int(
            room.get("decision_timeout_seconds") or DECISION_TIMEOUT_SECONDS
        )
    return snapshot


def set_player_connected(room_id: str, uid: str, connected: bool) -> None:
    for player in rooms[room_id]["players"]:
        if player["uid"] == uid:
            was_auto_play = bool(player.get("auto_play_enabled"))
            player["connection_seq"] = int(player.get("connection_seq", 0)) + 1
            player["connected"] = bool(connected)
            player["offline_deadline"] = None
            if connected:
                player["auto_play_enabled"] = False
                auto_ready_uids = rooms[room_id].setdefault("auto_ready_uids", set())
                if was_auto_play and uid in auto_ready_uids:
                    auto_ready_uids.discard(uid)
                    rooms[room_id].setdefault("next_round_votes", set()).discard(uid)
            return


def schedule_player_offline(room_id: str, uid: str) -> None:
    if room_id not in rooms:
        return

    for player in rooms[room_id]["players"]:
        if player["uid"] != uid:
            continue

        player["connection_seq"] = int(player.get("connection_seq", 0)) + 1
        connection_seq = player["connection_seq"]
        player["offline_deadline"] = time.time() + OFFLINE_TIMEOUT_SECONDS
        socketio.start_background_task(run_player_offline_timeout, room_id, uid, connection_seq)
        return


def run_player_offline_timeout(room_id: str, uid: str, connection_seq: int) -> None:
    socketio.sleep(OFFLINE_TIMEOUT_SECONDS)
    if room_id not in rooms or uid in online_account_ids():
        return

    player_meta = next((player for player in rooms[room_id]["players"] if player["uid"] == uid), None)
    if not player_meta or int(player_meta.get("connection_seq", 0)) != connection_seq:
        return

    set_player_connected(room_id, uid, False)
    room = rooms[room_id]
    if room["state"] in {"GAME", ROOM_HAND_ENDED}:
        player_meta["auto_play_enabled"] = True
        cancel_ai_for_room(room_id)
        socketio.emit(
            "auto_play_status",
            {"enabled": True, "reason": "OFFLINE_TIMEOUT"},
            room=uid,
        )
    emit_lobby_update(room_id)
    broadcast_game_state(room_id)
    if room["state"] == ROOM_HAND_ENDED:
        auto_ready_ai_players(room_id)
        emit_next_round_status(room_id)
        maybe_start_next_round_for_room(room_id)
    maybe_run_ai(room_id)


def set_player_ai_enabled(room_id: str, uid: str, enabled: bool) -> None:
    for player in rooms[room_id]["players"]:
        if player["uid"] == uid:
            player["ai_enabled"] = enabled
            return


def cancel_ai_for_room(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    room["ai_control_seq"] = int(room.get("ai_control_seq", 0)) + 1
    room["ai_busy"] = False


def is_ai_task_current(room_id: str, ai_control_seq: int) -> bool:
    return room_id in rooms and int(rooms[room_id].get("ai_control_seq", 0)) == ai_control_seq


def is_ai_controlled(room_id: str, uid: str) -> bool:
    for player in rooms[room_id]["players"]:
        if player["uid"] == uid:
            # Manual AI and the explicit 150-second offline takeover are separate.
            return bool(player.get("ai_enabled") or player.get("auto_play_enabled"))
    return False


def maybe_run_ai(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    game: GameEngine = room["game"]
    if room.get("ai_busy") or room["state"] != "GAME" or game.state == STATE_END_GAME:
        return

    needs_ai = False
    if game.state == STATE_PLAYER_TURN and game.players:
        needs_ai = is_ai_controlled(room_id, game.players[game.current_turn_idx].uid)
    elif game.state == STATE_WAIT_RESPONSE:
        responded = {response["uid"] for response in game.response_queue}
        needs_ai = any(is_ai_controlled(room_id, uid) and uid not in responded for uid in game.pending_uids)

    if needs_ai:
        room["ai_busy"] = True
        room["ai_control_seq"] = int(room.get("ai_control_seq", 0)) + 1
        socketio.start_background_task(run_ai_for_room, room_id, room["ai_control_seq"])


def schedule_decision_timeout(room_id: str) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    game: GameEngine = room["game"]
    if room["state"] != "GAME" or game.state not in {STATE_PLAYER_TURN, STATE_WAIT_RESPONSE}:
        clear_decision_timeout(room_id)
        return

    timeout_seconds = decision_timeout_seconds_for_room(room_id)
    room["decision_seq"] = int(room.get("decision_seq", 0)) + 1
    room["decision_timeout_seconds"] = timeout_seconds
    room["decision_deadline"] = time.time() + timeout_seconds
    payload = {
        "seconds": timeout_seconds,
        "deadline": room["decision_deadline"],
        "server_time": time.time(),
        "state": game.state,
        "current_turn_idx": game.current_turn_idx,
        "pending_uids": list(game.pending_uids),
    }
    socketio.emit("decision_timer", payload, room=room_id)
    socketio.start_background_task(run_decision_timeout, room_id, room["decision_seq"], timeout_seconds)


def decision_timeout_seconds_for_room(room_id: str) -> int:
    if room_id not in rooms or os.environ.get("AI_PROVIDER", "heuristic").strip().lower() != "ollama":
        return DECISION_TIMEOUT_SECONDS

    room = rooms[room_id]
    game: GameEngine = room["game"]
    needs_model_time = False
    if game.state == STATE_PLAYER_TURN and game.players:
        needs_model_time = is_ai_controlled(room_id, game.players[game.current_turn_idx].uid)
    elif game.state == STATE_WAIT_RESPONSE:
        responded = {response["uid"] for response in game.response_queue}
        needs_model_time = any(
            uid not in responded and is_ai_controlled(room_id, uid)
            for uid in game.pending_uids
        )

    if not needs_model_time:
        return DECISION_TIMEOUT_SECONDS

    try:
        configured = int(float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", AI_PROXY_DECISION_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        configured = AI_PROXY_DECISION_TIMEOUT_SECONDS
    return max(DECISION_TIMEOUT_SECONDS, configured)


def clear_decision_timeout(room_id: str) -> None:
    if room_id in rooms:
        rooms[room_id]["decision_deadline"] = None
        rooms[room_id]["decision_timeout_seconds"] = None
        socketio.emit("decision_timer_clear", {}, room=room_id)


def run_decision_timeout(room_id: str, decision_seq: int, timeout_seconds: Optional[int] = None) -> None:
    socketio.sleep(DECISION_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds)
    if room_id not in rooms:
        return
    room = rooms[room_id]
    if room.get("decision_seq") != decision_seq or room["state"] != "GAME":
        return

    game: GameEngine = room["game"]
    if game.state == STATE_WAIT_RESPONSE:
        # Humans pass on timeout; an existing Ollama proxy falls back to Python.
        timeout_pending_responses(room_id, decision_seq)
        return

    if game.state == STATE_PLAYER_TURN and game.players:
        clear_decision_timeout(room_id)
        timeout_discard_current_player(room_id, decision_seq)


def timeout_discard_current_player(room_id: str, decision_seq: int) -> None:
    if room_id not in rooms:
        return

    room = rooms[room_id]
    game: GameEngine = room["game"]
    if room.get("decision_seq") != decision_seq or room["state"] != "GAME":
        return
    if game.state != STATE_PLAYER_TURN or not game.players:
        return

    player = game.players[game.current_turn_idx]
    legal_indices = list(range(len(player.hand)))
    if player.declared_ting and game.last_drawn_tile:
        legal_indices = [
            index
            for index, tile in enumerate(player.hand)
            if tile == game.last_drawn_tile
        ]
    if not legal_indices:
        return

    python_takeover = (
        os.environ.get("AI_PROVIDER", "heuristic").strip().lower() == "ollama"
        and is_ai_controlled(room_id, player.uid)
    )
    if python_takeover:
        cancel_ai_for_room(room_id)
        tile_index = VisibleMahjongAI.choose_discard(game, player.idx, use_api=False)
        if tile_index not in legal_indices:
            tile_index = legal_indices[0]
        reason = "PYTHON_FALLBACK"
        automatic_reason = "AI_MODEL_TIMEOUT"
    else:
        tile_index = random.choice(legal_indices)
        reason = "RANDOM_DISCARD"
        automatic_reason = "TURN_TIMEOUT"
    socketio.emit(
        "turn_timeout",
        {
            "uid": player.uid,
            "player_idx": player.idx,
            "tile_index": tile_index,
            "reason": reason,
        },
        room=room_id,
    )
    try:
        discard_for_player(
            room_id,
            player.uid,
            tile_index,
            automatic_reason=automatic_reason,
        )
    except ValueError:
        if room_id in rooms and rooms[room_id].get("decision_seq") == decision_seq:
            schedule_decision_timeout(room_id)


def timeout_pending_responses(room_id: str, decision_seq: int) -> None:
    game: GameEngine = rooms[room_id]["game"]
    while game.state == STATE_WAIT_RESPONSE and rooms[room_id].get("decision_seq") == decision_seq:
        responded = {response["uid"] for response in game.response_queue}
        waiting = [uid for uid in game.pending_uids if uid not in responded]
        if not waiting:
            return

        uid = waiting[0]
        use_python_takeover = (
            os.environ.get("AI_PROVIDER", "heuristic").strip().lower() == "ollama"
            and is_ai_controlled(room_id, uid)
        )
        if use_python_takeover:
            cancel_ai_for_room(room_id)
            actions = rooms[room_id].get("pending_actions_by_uid", {}).get(uid, [])
            action = VisibleMahjongAI.choose_response(game, uid, actions, use_api=False)
            result = game.submit_response(
                uid,
                action.get("type", "PASS"),
                action.get("tile"),
                action.get("tiles"),
            )
        else:
            result = game.submit_response(uid, "PASS")
        handle_response_result(room_id, result)
        if result["status"] != "WAITING":
            return


def run_ai_for_room(room_id: str, ai_control_seq: int) -> None:
    try:
        for _ in range(AI_MAX_STEPS_PER_TASK):
            socketio.sleep(AI_STEP_DELAY_SECONDS)
            if not is_ai_task_current(room_id, ai_control_seq):
                return
            if not perform_ai_step(room_id, ai_control_seq):
                break
    finally:
        if is_ai_task_current(room_id, ai_control_seq):
            rooms[room_id]["ai_busy"] = False
            maybe_run_ai(room_id)


def perform_ai_step(room_id: str, ai_control_seq: Optional[int] = None) -> bool:
    if room_id not in rooms or rooms[room_id]["state"] != "GAME":
        return False
    if ai_control_seq is not None and not is_ai_task_current(room_id, ai_control_seq):
        return False

    room = rooms[room_id]
    game: GameEngine = room["game"]

    if game.state == STATE_WAIT_RESPONSE:
        responded = {response["uid"] for response in game.response_queue}
        for uid in list(game.pending_uids):
            if uid in responded or not is_ai_controlled(room_id, uid):
                continue
            actions = room.get("pending_actions_by_uid", {}).get(uid, [])
            action = VisibleMahjongAI.choose_response(game, uid, actions)
            if (
                not is_ai_controlled(room_id, uid)
                or (ai_control_seq is not None and not is_ai_task_current(room_id, ai_control_seq))
            ):
                return False
            result = game.submit_response(uid, action.get("type", "PASS"), action.get("tile"), action.get("tiles"))
            handle_response_result(room_id, result)
            return True
        return False

    if game.state != STATE_PLAYER_TURN or not game.players:
        return False

    player = game.players[game.current_turn_idx]
    if not is_ai_controlled(room_id, player.uid):
        return False

    self_actions = game.check_self_actions()
    hu = next((action for action in self_actions if action["type"] == "HU"), None)
    if hu:
        if not is_ai_controlled(room_id, player.uid):
            return False
        result = game.apply_self_action(player.uid, "HU", hu.get("tile"))
        handle_action_result(room_id, result)
        return True

    for action in self_actions:
        if action["type"] in {"ANKANG", "BUKANG"}:
            if not is_ai_controlled(room_id, player.uid):
                return False
            result = game.apply_self_action(player.uid, action["type"], action.get("tile"))
            handle_action_result(room_id, result)
            return True

    ting = next((action for action in self_actions if action["type"] == "TING"), None)
    if ting:
        if not is_ai_controlled(room_id, player.uid):
            return False
        discard_for_player(
            room_id,
            player.uid,
            int(ting["tile_index"]),
            declare_ting=True,
        )
        return True

    tile_index = VisibleMahjongAI.choose_discard(game, player.idx)
    if (
        not is_ai_controlled(room_id, player.uid)
        or (ai_control_seq is not None and not is_ai_task_current(room_id, ai_control_seq))
    ):
        return False
    try:
        discard_for_player(room_id, player.uid, tile_index)
    except ValueError:
        if game.state == STATE_PLAYER_TURN and game.players[game.current_turn_idx].uid == player.uid and player.hand:
            try:
                discard_for_player(room_id, player.uid, 0)
            except ValueError:
                return False
        else:
            return False
    return True


def clear_pending_actions(room_id: str) -> None:
    if room_id in rooms:
        rooms[room_id]["pending_actions_by_uid"] = {}
        socketio.emit("clear_actions", room=room_id)


def emit_discard_event(
    room_id: str,
    uid: str,
    tile_index: int,
    client_action_id: Optional[str] = None,
    automatic_reason: Optional[str] = None,
) -> None:
    if room_id not in rooms:
        return

    game: GameEngine = rooms[room_id]["game"]
    player = game.get_player(uid)
    if not player or not game.last_discard:
        return

    payload = {
        "type": "DISCARD",
        "player_uid": uid,
        "player_idx": player.idx,
        "tile": game.last_discard["tile"],
        "tile_index": tile_index,
        "state_seq": int(rooms[room_id].get("state_seq", 0)) + 1,
        "hand_count_after": len(player.hand),
        "discard_count_after": len(player.discards),
    }
    if client_action_id:
        payload["client_action_id"] = client_action_id
    if automatic_reason:
        payload["automatic_reason"] = automatic_reason
    socketio.emit("game_event", payload, room=room_id)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    if sys.stdout:
        print(f"Server starting on http://127.0.0.1:{port}")
    socketio.run(app, debug=debug, host="0.0.0.0", port=port)
