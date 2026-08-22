from collections import Counter
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

try:
    from eventlet import sleep as cooperative_sleep
    from eventlet import tpool as eventlet_tpool
except ImportError:  # pragma: no cover - eventlet is a runtime dependency.
    cooperative_sleep = time.sleep
    eventlet_tpool = None

from .env_loader import load_env
from .knowledge import build_visible_state
from .rules import DRAGON_TILES, WIND_TILES, all_play_tiles, is_suited, tile_sort_key
from .strategy import calculate_shanten, get_vector_score


load_env()

_API_LOCK = threading.Lock()
_API_SEMAPHORE = threading.BoundedSemaphore(max(1, int(os.environ.get("AI_MAX_CONCURRENT_REQUESTS", "1") or "1")))
_LAST_API_CALL_AT = 0.0
_CACHE: Dict[str, Dict] = {}
LOGGER = logging.getLogger(__name__)


class VisibleMahjongAI:
    @staticmethod
    def choose_discard(game, player_idx: int, use_api: bool = True) -> int:
        player = game.players[player_idx]
        if not player.hand:
            return 0

        visible_state = build_visible_state(game, player.uid, compact=True)
        if use_api:
            choice = AdvisorClient.choose_discard(visible_state)
            if isinstance(choice, int) and 0 <= choice < len(player.hand):
                return choice

        return _heuristic_discard(visible_state)

    @staticmethod
    def choose_response(game, uid: str, actions: List[Dict], use_api: bool = True) -> Dict:
        if not actions:
            return {"type": "PASS"}

        visible_state = build_visible_state(game, uid, available_actions=actions, compact=True)
        if use_api:
            choice = AdvisorClient.choose_action(visible_state, actions)
            matched = _match_action(choice, actions)
            if matched:
                return matched

        return _heuristic_response(visible_state, actions)

    @staticmethod
    def random_discard(game, player_idx: int) -> int:
        hand = game.players[player_idx].hand
        return random.randrange(len(hand)) if hand else 0


class AdvisorClient:
    @staticmethod
    def choose_discard(visible_state: Dict) -> Optional[int]:
        hand = visible_state.get("self", {}).get("hand", [])
        if not hand:
            return None

        response = _call_model(
            {
                "task": "choose_discard",
                "rules": "Taiwanese 16-tile mahjong. Use only the visible_state. Hidden hands and wall order are unknown.",
                "required_json": {"action": "DISCARD", "tile_index": "integer index from self.hand"},
                "visible_state": visible_state,
            }
        )
        if response and str(response.get("action", "")).upper() == "DISCARD":
            index = response.get("tile_index")
            if isinstance(index, int):
                return index
        return None

    @staticmethod
    def choose_action(visible_state: Dict, actions: List[Dict]) -> Optional[Dict]:
        return _call_model(
            {
                "task": "choose_claim_or_pass",
                "rules": "Taiwanese 16-tile mahjong. Use only the visible_state. Hidden hands and wall order are unknown.",
                "required_json": {"action": "one offered action type or PASS", "tiles": "only for CHI when needed"},
                "visible_state": visible_state,
                "available_actions": actions,
            }
        )


def _call_model(payload: Dict) -> Optional[Dict]:
    provider = os.environ.get("AI_PROVIDER", "heuristic").strip().lower()
    if provider == "heuristic":
        return None

    cache_payload = {
        "provider": provider,
        "model": _provider_model(provider),
        "payload": payload,
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached = _CACHE.get(cache_key)
    ttl = _env_float("AI_CACHE_TTL_SECONDS", 45.0)
    if cached and time.time() - cached["time"] < ttl:
        return cached["value"]

    if not _API_SEMAPHORE.acquire(blocking=False):
        return None

    try:
        _wait_for_rate_limit()
        if provider == "ollama":
            result = _call_ollama(payload)
        elif provider in {"openai", "openai_compatible", "nvidia"}:
            result = _call_openai_compatible(payload)
        elif provider == "gemini":
            result = _call_gemini(payload)
        else:
            result = None
    finally:
        _API_SEMAPHORE.release()

    if result:
        _CACHE[cache_key] = {"time": time.time(), "value": result}
    return result


def _wait_for_rate_limit() -> None:
    global _LAST_API_CALL_AT
    cooldown = _env_float("AI_REQUEST_COOLDOWN_SECONDS", 1.5)
    with _API_LOCK:
        now = time.time()
        scheduled_at = max(now, _LAST_API_CALL_AT + cooldown)
        _LAST_API_CALL_AT = scheduled_at
    wait_seconds = scheduled_at - now
    if wait_seconds > 0:
        cooperative_sleep(wait_seconds)


def _provider_model(provider: str) -> str:
    if provider == "ollama":
        return os.environ.get("OLLAMA_MODEL", "qwen3:4b").strip()
    if provider == "gemini":
        return os.environ.get("GEMINI_MODEL", os.environ.get("AI_MODEL", "gemini-2.5-flash")).strip()
    return os.environ.get("AI_MODEL", "").strip()


def _call_ollama(payload: Dict) -> Optional[Dict]:
    base_url = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen3:4b").strip()
    if not model:
        return None

    response_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "tile_index": {"type": "integer"},
            "tiles": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    user_prompt = (
        "Choose exactly one action now. Do not repeat or summarize the input. "
        "Return only the smallest JSON object that satisfies the response schema.\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "think": False,
            "format": response_schema,
            "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "10m"),
            "options": {
                "temperature": 0.1,
                "num_predict": 180,
            },
            "messages": [
                {"role": "system", "content": _system_instruction()},
                {"role": "user", "content": user_prompt},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    raw = _urlopen_json(request, timeout=_env_float("OLLAMA_TIMEOUT_SECONDS", 120.0))
    if not raw:
        return None
    try:
        text = raw["message"]["content"]
    except (KeyError, TypeError):
        LOGGER.warning("Ollama response did not contain message.content")
        return None
    parsed = _parse_json_object(text)
    if parsed is None:
        LOGGER.warning("Ollama returned non-JSON content (%s characters)", len(str(text)))
    return parsed


def _call_gemini(payload: Dict) -> Optional[Dict]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("AI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.environ.get("GEMINI_MODEL", os.environ.get("AI_MODEL", "gemini-2.5-flash")).strip()
    base_url = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    url = f"{base_url}/models/{model}:generateContent"
    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": _system_instruction()}]},
            "contents": [{"parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    raw = _urlopen_json(request)
    return _parse_json_object(_extract_gemini_text(raw)) if raw else None


def _call_openai_compatible(payload: Dict) -> Optional[Dict]:
    api_key = os.environ.get("AI_API_KEY", "").strip()
    if not api_key:
        return None

    base_url = os.environ.get("AI_API_BASE", "https://integrate.api.nvidia.com/v1").rstrip("/")
    model = os.environ.get("AI_MODEL", "").strip()
    if not model:
        return None

    body = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": _system_instruction()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    raw = _urlopen_json(request)
    if not raw:
        return None
    try:
        text = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return _parse_json_object(text)


def _system_instruction() -> str:
    return (
        "You are a fair AI substitute for an online Taiwanese mahjong player. "
        "This table uses Taiwanese 16-tile mahjong: a standard win is five melds and one pair. "
        "You receive only that player's legal visible information and the legal actions offered by the server. "
        "Never invent an action, tile, hand index, hidden hand, or wall order. "
        "If HU is offered, choose HU. Passing a legal HU causes Guoshui until a safe draw-discard cycle, "
        "and Guoshui also cancels the Di-Ting bonus. "
        "The first HU response received by the server wins; this table does not resolve multiple winners. "
        "Declared Ting locks the hand. Di-Ting depends on the declarer's own first discard and having no meld; "
        "other players' claims do not cancel it. "
        "After declared Ting, a MingKang replacement draw cannot self-draw, while AnKang and BuKang replacements can. "
        "For CHI, copy one exact tiles combination from available_actions. "
        "Return JSON only, with no markdown."
    )


def _urlopen_json(request: urllib.request.Request, timeout: Optional[float] = None) -> Optional[Dict]:
    request_timeout = timeout if timeout is not None else _env_float("AI_TIMEOUT_SECONDS", 8.0)

    def fetch() -> Dict:
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        if eventlet_tpool is not None:
            return eventlet_tpool.execute(fetch)
        return fetch()
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        LOGGER.warning("AI provider request failed: %s", exc)
        return None


def _extract_gemini_text(response: Dict) -> str:
    try:
        parts = response["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return ""
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))


def _parse_json_object(text: str) -> Optional[Dict]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return None
        cleaned = match.group(0)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _heuristic_discard(visible_state) -> int:
    hand, original_indices = _extract_hand(visible_state)
    if not hand:
        return 0

    context = _strategy_context(visible_state)
    best_idx = original_indices[0]
    best_score = None
    for idx, tile in enumerate(hand):
        remaining = hand[:idx] + hand[idx + 1 :]
        score = _discard_score(tile, hand, remaining, context)
        if best_score is None or score > best_score:
            best_score = score
            best_idx = original_indices[idx]

    return best_idx


def _to_34_counts(hand: List[str]) -> List[int]:
    counts = [0] * 34
    for tile in hand:
        try:
            value = int(tile[:-1]) - 1
        except ValueError:
            continue
        if tile.endswith("z"):
            if 0 <= value < 7:
                counts[27 + value] += 1
            continue
        offset = {"m": 0, "p": 9, "s": 18}.get(tile[-1])
        if offset is not None and 0 <= value < 9:
            counts[offset + value] += 1
    return counts


def _discard_score(tile: str, original: List[str], remaining: List[str], context: Dict) -> float:
    shanten = _estimate_shanten(remaining, context["meld_count"])
    effective_tiles = _effective_tile_count(remaining, context, shanten)
    shape_score = get_vector_score(remaining)
    discard_pressure = _discard_pressure(tile, original, context)
    safety_score = _visible_safety_score(tile, context)
    value_penalty = _value_keep_score(tile, original, context)

    return (
        (-1000 * shanten)
        + (8 * effective_tiles)
        + shape_score
        + discard_pressure
        + safety_score
        - value_penalty
    )


def _heuristic_response(visible_state: Dict, actions: List[Dict]) -> Dict:
    hu = next((action for action in actions if action["type"] == "HU"), None)
    if hu:
        return hu

    hand, _indices = _extract_hand(visible_state)
    if not hand:
        return {"type": "PASS"}

    context = _strategy_context(visible_state)
    current_shanten = _estimate_shanten(hand, context["meld_count"])
    best_action = None
    best_score = 0.0
    for action in actions:
        if action["type"] == "PASS":
            continue
        score = _claim_score(hand, action, context, current_shanten)
        if score > best_score:
            best_score = score
            best_action = action

    return best_action if best_action and best_score >= _claim_threshold(best_action) else {"type": "PASS"}


def _claim_score(hand: List[str], action: Dict, context: Dict, current_shanten: int) -> float:
    remaining = _hand_after_claim(hand, action)
    if remaining is None:
        return -9999

    new_context = dict(context)
    new_context["meld_count"] = context["meld_count"] + 1
    new_shanten = _estimate_shanten(remaining, new_context["meld_count"])
    effective_before = _effective_tile_count(hand, context, current_shanten)
    effective_after = _effective_tile_count(remaining, new_context, new_shanten)
    improvement = current_shanten - new_shanten
    tile = action.get("tile", "")

    score = (
        (120 * improvement)
        + (5 * (effective_after - effective_before))
        + (get_vector_score(remaining) * 0.35)
        + _claim_value_score(tile, action, context)
    )
    if action["type"] == "KANG":
        score += 26 if improvement >= 0 else -18
    elif action["type"] == "PON":
        score += 8
    elif action["type"] == "CHI":
        score += _chi_shape_bonus(action)
    return score


def _claim_threshold(action: Dict) -> float:
    return {"KANG": 28.0, "PON": 18.0, "CHI": 24.0}.get(action.get("type"), 999.0)


def _hand_after_claim(hand: List[str], action: Dict) -> Optional[List[str]]:
    remaining = list(hand)
    action_type = action["type"]
    tiles = list(action.get("tiles") or [])
    if action_type == "CHI":
        needed = tiles
    elif action_type == "PON":
        needed = [action.get("tile")] * 2
    elif action_type == "KANG":
        needed = [action.get("tile")] * 3
    else:
        return None

    for tile in needed:
        if not tile or tile not in remaining:
            return None
        remaining.remove(tile)
    return remaining


def _extract_hand(visible_state) -> Tuple[List[str], List[int]]:
    if isinstance(visible_state, list):
        return list(visible_state), list(range(len(visible_state)))

    raw_hand = visible_state.get("self", {}).get("hand", []) if isinstance(visible_state, dict) else []
    hand: List[str] = []
    indices: List[int] = []
    for idx, item in enumerate(raw_hand):
        if isinstance(item, dict):
            hand.append(item.get("tile", ""))
            indices.append(int(item.get("i", idx)))
        else:
            hand.append(item)
            indices.append(idx)
    return hand, indices


def _strategy_context(visible_state) -> Dict:
    self_view = visible_state.get("self", {}) if isinstance(visible_state, dict) else {}
    unknown_counts = visible_state.get("unknown_tile_counts", {}) if isinstance(visible_state, dict) else {}
    if not unknown_counts:
        unknown_counts = {tile: 4 for tile in all_play_tiles()}

    return {
        "unknown_counts": unknown_counts,
        "wall_remaining_count": int(visible_state.get("wall_remaining_count", 0)) if isinstance(visible_state, dict) else 0,
        "round_wind": int(visible_state.get("round_wind", 0)) if isinstance(visible_state, dict) else 0,
        "seat_wind": (
            int(self_view.get("idx", 0) or 0)
            - int(visible_state.get("dealer_idx", 0) or 0)
        ) % 4,
        "meld_count": len(self_view.get("melds", [])),
    }


def _estimate_shanten(hand: List[str], meld_count: int = 0) -> int:
    return max(-1, calculate_shanten(_to_34_counts(hand)) - (2 * meld_count))


def _effective_tile_count(hand: List[str], context: Dict, current_shanten: Optional[int] = None) -> int:
    if current_shanten is None:
        current_shanten = _estimate_shanten(hand, context["meld_count"])

    total = 0
    for tile in all_play_tiles():
        unknown = int(context["unknown_counts"].get(tile, 0) or 0)
        if unknown <= 0:
            continue
        if _estimate_shanten(hand + [tile], context["meld_count"]) < current_shanten:
            total += unknown
    return total


def _discard_pressure(tile: str, hand: List[str], context: Dict) -> int:
    pressure = 0
    count = hand.count(tile)
    if count >= 3:
        pressure -= 65
    elif count == 2:
        pressure -= 34

    if tile.endswith("z"):
        pressure += 26 if count == 1 else -12
    elif _is_terminal(tile):
        pressure += 16
    elif _is_isolated(tile, hand):
        pressure += 24

    off_suit = _off_suit_pressure(tile, hand)
    return pressure + off_suit


def _visible_safety_score(tile: str, context: Dict) -> int:
    wall_remaining = context["wall_remaining_count"]
    if wall_remaining > 42:
        return 0
    unknown = int(context["unknown_counts"].get(tile, 0) or 0)
    return (4 - min(4, unknown)) * (7 if wall_remaining <= 24 else 3)


def _value_keep_score(tile: str, hand: List[str], context: Dict) -> int:
    count = hand.count(tile)
    if tile in DRAGON_TILES:
        return 42 if count >= 2 else 8
    seat_tile = f"{context['seat_wind'] + 1}z"
    round_tile = f"{context['round_wind'] + 1}z"
    if tile in {seat_tile, round_tile}:
        return 36 if count >= 2 else 6
    if tile in WIND_TILES:
        return 18 if count >= 2 else 3
    return 0


def _claim_value_score(tile: str, action: Dict, context: Dict) -> int:
    if tile in DRAGON_TILES:
        return 44
    if tile in {f"{context['seat_wind'] + 1}z", f"{context['round_wind'] + 1}z"}:
        return 38
    if tile in WIND_TILES:
        return 18
    if action["type"] == "KANG":
        return 12
    return 0


def _chi_shape_bonus(action: Dict) -> int:
    tiles = list(action.get("tiles") or []) + [action.get("tile")]
    if len(tiles) != 3 or not all(tile and is_suited(tile) for tile in tiles):
        return 0
    ranks = sorted(int(tile[:-1]) for tile in tiles)
    if ranks in ([1, 2, 3], [7, 8, 9]):
        return -8
    return 10


def _is_terminal(tile: str) -> bool:
    if not tile or tile.endswith("z"):
        return False
    try:
        return int(tile[:-1]) in {1, 9}
    except ValueError:
        return False


def _is_isolated(tile: str, hand: List[str]) -> bool:
    if not is_suited(tile) or hand.count(tile) > 1:
        return False
    rank = int(tile[:-1])
    suit = tile[-1]
    neighbors = {f"{rank + delta}{suit}" for delta in (-2, -1, 1, 2) if 1 <= rank + delta <= 9}
    return not any(neighbor in hand for neighbor in neighbors)


def _off_suit_pressure(tile: str, hand: List[str]) -> int:
    if not is_suited(tile):
        return 0
    suited_counts = Counter(item[-1] for item in hand if is_suited(item))
    if not suited_counts:
        return 0
    main_suit, main_count = suited_counts.most_common(1)[0]
    honor_count = sum(1 for item in hand if item.endswith("z"))
    if main_count + honor_count < max(10, len(hand) - 4):
        return 0
    return 34 if tile[-1] != main_suit else -10


def _match_action(choice: Optional[Dict], actions: List[Dict]) -> Optional[Dict]:
    if not choice:
        return None

    action_type = choice.get("action") or choice.get("type")
    if isinstance(action_type, str):
        action_type = action_type.upper()
    if not action_type or action_type == "PASS":
        return {"type": "PASS"}

    for action in actions:
        if action["type"] != action_type:
            continue
        if action_type == "CHI" and choice.get("tiles") and not _same_tiles(action.get("tiles", []), choice.get("tiles", [])):
            continue
        return action
    return None


def _same_tiles(left: List[str], right: List[str]) -> bool:
    return sorted(left, key=tile_sort_key) == sorted(right, key=tile_sort_key)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
