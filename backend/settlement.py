from typing import Dict, Iterable, Optional


def settle_single_winner(
    player_uids: Iterable[str],
    winner_uid: str,
    fan_total: int,
    is_zimo: bool,
    payer_uid: Optional[str] = None,
    payment_units_by_uid: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    uids = list(player_uids)
    deltas = {uid: 0 for uid in uids}
    fan_total = max(0, int(fan_total))
    if winner_uid not in deltas or fan_total <= 0:
        return deltas

    if is_zimo:
        for uid in uids:
            if uid == winner_uid:
                continue
            payment = max(
                0,
                int((payment_units_by_uid or {}).get(uid, fan_total)),
            )
            deltas[uid] -= payment
            deltas[winner_uid] += payment
    elif payer_uid in deltas and payer_uid != winner_uid:
        payment = max(
            0,
            int((payment_units_by_uid or {}).get(payer_uid, fan_total)),
        )
        deltas[payer_uid] -= payment
        deltas[winner_uid] += payment

    if sum(deltas.values()) != 0:
        raise ValueError("結算分數必須保持零和")
    return deltas
