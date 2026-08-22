
# Simplified Shanten Calculator
# Goal: Calculate minimum tiles needed to reach 5 sets + 1 pair.

def calculate_shanten(hand_34):
    """
    hand_34 is a list of counts of tiles [0..33].
    0-8: Man, 9-17: Pin, 18-26: Sou, 27-33: Honors
    Returns shanten number (0 = Tenpai/Ready, -1 = Hu)
    Target: 5 Sets + 1 Pair (Standard)
    """
    # This is a complex recursive problem (standard optimization).
    # Since we need "Fast" and "Smart Enough", we use a generic backtracking search.
    
    current_min = 8 # Max shanten usually around 6-8
    
    def search(depth, sets, pairs, current_hand):
        nonlocal current_min
        
        # Pruning
        if depth + (5 - sets)*2/3 - pairs > current_min: 
             return
             
        if sets == 5 and pairs == 1:
            if depth < current_min:
                current_min = depth
            return

        # Find first non-zero tile
        first = -1
        for i in range(34):
            if current_hand[i] > 0:
                first = i
                break
                
        if first == -1:
            # Hand empty, check result
            # Assuming we consumed everything needed
            # Remaining needed = (5-sets)*3 + (1-pairs)*2 ... wait
            # Hand size reduces as we form sets.
            # Shanten = 8 - (2*sets) - pairs? No.
            # Shanten = (Required Sets * 3 + Required Pair * 2 - Existing Melds) / ...
            # Actually, standard algorithm calculates "groups" and "tatsu (partial groups)".
            # Shanten = 8 - (2 * groups) - partials - pair_bonus
            return

        # Try to form SET (Koutsu/Triplet)
        if current_hand[first] >= 3:
            current_hand[first] -= 3
            search(depth, sets+1, pairs, current_hand)
            current_hand[first] += 3
            
        # Try to form SET (Shuntsu/Sequence) - Only neighbors
        if first < 27: # Non-honor
            suit = first // 9
            val = first % 9
            if val <= 6: # Can start sequence
                if current_hand[first+1] > 0 and current_hand[first+2] > 0:
                    current_hand[first] -= 1
                    current_hand[first+1] -= 1
                    current_hand[first+2] -= 1
                    search(depth, sets+1, pairs, current_hand)
                    current_hand[first] += 1
                    current_hand[first+1] += 1
                    current_hand[first+2] += 1
                    
        # Try to form PAIR
        if pairs == 0 and current_hand[first] >= 2:
            current_hand[first] -= 2
            search(depth, sets, pairs+1, current_hand)
            current_hand[first] += 2
            
        # Skip this tile
        temp = current_hand[first]
        current_hand[first] = 0
        search(depth + temp, sets, pairs, current_hand)
        current_hand[first] = temp

    search(0, 0, 0, list(hand_34))
    return current_min - 1

def get_vector_score(hand):
    """
    Heuristic Score: Higher is better.
    hand: List of strings ['1m', '2m'...]
    """
    from collections import Counter
    score = 0
    counts = Counter(hand)
    
    # Analyze by suit
    suits = {'m': set(), 'p': set(), 's': set(), 'z': set()}
    for t in hand:
        s = t[-1]
        if s in suits:
            val = int(t[:-1])
            suits[s].add(val)
        
    for s in ['m', 'p', 's']:
        vals = sorted(list(suits[s]))
        for v in vals:
            tile = f"{v}{s}"
            count = counts[tile]
            
            # Triplets
            if count >= 3:
                score += 300
            # Pair
            elif count == 2:
                score += 50
                
            # Sequence Neighbors
            if (v+1) in vals and (v+2) in vals:
                score += 300
            elif (v+1) in vals:
                score += 100 # Good waiter
            elif (v+2) in vals:
                score += 80 # Middle waiter (Kan-chan)

    # Honors
    for v in suits['z']:
        tile = f"{v}z"
        count = counts[tile]
        if count >= 3: score += 300
        elif count == 2: score += 50
        
    return score
