import json
import os
import sys
import time


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.game_engine import GameEngine
from backend.knowledge import build_visible_state
from backend.visible_ai import _call_ollama


def main() -> int:
    game = GameEngine()
    game.add_player("ollama-smoke", "Ollama Smoke")
    game.players[0].hand = [
        "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m",
        "2p", "3p", "4p", "5p", "5p",
        "7s", "8s", "9s",
        "1z",
    ]
    visible_state = build_visible_state(game, "ollama-smoke", compact=True)
    payload = {
        "task": "choose_discard",
        "rules": "Taiwanese 16-tile mahjong. Use only the visible_state. Hidden hands and wall order are unknown.",
        "required_json": {"action": "DISCARD", "tile_index": "integer index from self.hand"},
        "visible_state": visible_state,
    }

    started_at = time.monotonic()
    response = _call_ollama(payload)
    elapsed = time.monotonic() - started_at
    result = {
        "model": os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
        "elapsed_seconds": round(elapsed, 2),
        "response": response,
    }
    print(json.dumps(result, ensure_ascii=False))

    if not response or str(response.get("action", "")).upper() != "DISCARD":
        return 1
    tile_index = response.get("tile_index")
    if not isinstance(tile_index, int) or not 0 <= tile_index < len(game.players[0].hand):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
